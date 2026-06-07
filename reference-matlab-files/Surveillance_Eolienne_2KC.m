%% ============================================================
% SURVEILLANCE EOLIENNES - MAT HYBRIDE BOIS-ACIER
% Version 5.5 - Corrections complètes + refonte déplacement tête de tour
%
% CORRECTIONS v5.5 vs v5.4 :
%   [BUG1]  CSTOP -> C_STOP (crash Fig 4, §FIG4)
%   [BUG2]  yl42_hi définie explicitement avant usage
%   [BUG3]  Legend >50 entrees : DisplayName supprime des scatter dans boucle
%   [BUG4]  Conflit colorbar/subplot : colorbar créée APRES toutes les subplots
%   [BUG5]  has_disp_ref non défini si ref externe absente -> test sécurisé
%   [BUG6]  p95_disp_res_ax non défini si has_disp_ref=false -> fallback garanti
%   [BUG7]  Variables disp_* recalculées par double intégration spectrale correcte
%           (division par (2*pi*f)^4 fréquence PAR fréquence, pas f_centre fixe)
%   [CALCUL] Déplacement : intégration fréquentielle rigoureuse depuis la PSD
%            D_rms(bande) = sqrt(sum(Pxx(f)/(2*pi*f)^4 * df)) [m] -> mm
%            Facteur fenêtre de Hamming compensé correctement
%   [CALCUL] D_amp temporel (Fig4 + Fig5) : filtre passe-bande 2x intégration
%            numerique par cumtrapz (plus robuste que IFFT avec poids)
%% ============================================================
clear; close all; clc;
set(0,'DefaultAxesFontSize',16);
set(0,'DefaultAxesFontName','Arial');
set(0,'DefaultTextFontSize',16);
set(0,'DefaultTextFontName','Arial');
set(0,'DefaultLegendFontSize',14);
set(0,'DefaultAxesTitleFontSizeMultiplier',1.05);

%% 1. SELECTION TURBINE
turbines_disp = {'W003','W005','W007'};
[idx_t,ok_t]=listdlg('PromptString','Selectionner la turbine :',...
    'SelectionMode','single','ListString',turbines_disp,'ListSize',[220 100],'Name','Turbine');
if ~ok_t, error('Annule.'); end
turbine_id = lower(turbines_disp{idx_t});
fprintf('=== Turbine : %s ===\n', upper(turbine_id));

%% 2. PARAMETRES
switch turbine_id
    case 'w003'; cutin_ms=2.50; v_z2_min=3.8; v_z2_max=8.5; P_nom_kW=500;  rpm_nom=13.0; f_rpm_res=7.0; rpm_marg=1.0; H_tour=100;
    case 'w005'; cutin_ms=2.75; v_z2_min=4.0; v_z2_max=8.5; P_nom_kW=2000; rpm_nom=13.0; f_rpm_res=7.0; rpm_marg=1.0; H_tour=100;
    case 'w007'; cutin_ms=2.25; v_z2_min=3.8; v_z2_max=8.5; P_nom_kW=2000; rpm_nom=13.0; f_rpm_res=7.0; rpm_marg=1.0; H_tour=112;
    otherwise, error('Turbine inconnue.');
end
P_zone2_max=0.85*P_nom_kW;
col_time_acc='datetime'; col_ax='ax'; col_ay='ay';
col_time_sc='pointTime';
col_power=[turbine_id 'Power']; col_speed=[turbine_id 'Speed']; col_rpm_sc=[turbine_id 'RotorSpeed'];
tz_acc='Europe/Brussels'; tz_sc='UTC';
f_lo=0.20; f_hi=0.40; f_s_lo=0.31; f_s_hi=0.34;
% --- Bandes RMS ---
f_bf_lo=0.05; f_bf_hi=0.25;   % Basse fréquence (turbulence, rafales)
f_res_lo=0.25; f_res_hi=0.35; % Résonance (mode propre de la tour)
f_bb_lo=0.05;  f_bb_hi=0.45;  % Broadband conservé pour affichage contexte
harm_bw=0.020;
win_min=10; ovlp=0.50; N_trans=3; tol_f0=0.05; seuil_kW=10; v_min_ctx=5.0; p_min_ctx=0.05;
ref_mat_file = '';
ewma_win=19; drift_alert=0.001;
fdd_seg_min=30; fdd_ovlp=0.50; fdd_f_lo=0.10; fdd_f_hi=0.50; hp_bw_frac=0.707;
fmt_list={'yyyy-MM-dd HH:mm:ss','yyyy-MM-dd HH:mm:ss.SSSSSS','dd/MM/yyyy HH:mm:ss','MM/dd/yyyy HH:mm:ss'};

%% 3. CHARGEMENT ACCELERO
fprintf('=== Fichiers accelero ===\n');
[fn_a,fp_a]=uigetfile('*.csv','Accelero (Ctrl+clic=multi)','MultiSelect','on');
if isequal(fn_a,0), error('Annule.'); end
if ischar(fn_a), fn_a={fn_a}; end
fn_a=sort(fn_a(:)');
T_acc=[];
for k=1:numel(fn_a)
    try
        Tk=readtable(fullfile(fp_a,fn_a{k}),'TextType','string');
        cv=Tk.(col_time_acc);
        if ~isdatetime(cv)
            ok_f=false;
            for f_fmt=fmt_list
                try; cv=datetime(cv,'InputFormat',f_fmt{1},'TimeZone',tz_acc); ok_f=true; break; catch, end
            end
            if ~ok_f, error('Format datetime non reconnu.'); end
        else
            if isempty(cv.TimeZone), cv.TimeZone=tz_acc; end
        end
        Tk.(col_time_acc)=cv;
        T_acc=[T_acc;Tk]; %#ok<AGROW>
        fprintf('  %s : %d pts\n',fn_a{k},height(Tk));
    catch ME
        fprintf('  ERREUR %s : %s\n',fn_a{k},ME.message);
    end
end
T_acc=sortrows(T_acc,{col_time_acc},{'ascend'});
t_loc=T_acc.(col_time_acc);
t_utc=t_loc; t_utc.TimeZone='UTC'; t_utc.Format='yyyy-MM-dd HH:mm:ss Z';
N=height(T_acc);

% --- Detection fs robuste ---
dt_med=seconds(median(diff(t_utc(1:min(500,N)))));
if dt_med<=0 || ~isfinite(dt_med)
    t_span=seconds(t_utc(end)-t_utc(1));
    if t_span>0 && N>1
        dt_med=t_span/(N-1);
        fprintf('  Timestamps peu resolus -> fs estime depuis duree totale\n');
    else
        dt_med=1;
    end
end
fs=max(1,round(1/dt_med));
fprintf('  fs=%d Hz | %d pts | %.1f h\n',fs,N,N/fs/3600);

%% 4. CHARGEMENT SCADA
fprintf('=== Fichiers SCADA ===\n');
[fn_s,fp_s]=uigetfile('*.csv','SCADA (Annuler=sans)','MultiSelect','on');
has_sc=~isequal(fn_s,0);
power_ts=nan(N,1); wind_ts=nan(N,1); rpm_sc=nan(N,1);
has_pow=false; has_wind=false; has_rpm_dir=false;
if has_sc
    if ischar(fn_s), fn_s={fn_s}; end
    T_sc=[];
    for k=1:numel(fn_s)
        try
            Tk=readtable(fullfile(fp_s,fn_s{k}),'TextType','string');
            cv=Tk.(col_time_sc);
            if ~isdatetime(cv)
                ok_f=false;
                for f_fmt=fmt_list
                    try; cv=datetime(cv,'InputFormat',f_fmt{1},'TimeZone',tz_sc); ok_f=true; break; catch, end
                end
                if ~ok_f, error('Format datetime non reconnu.'); end
            else
                if isempty(cv.TimeZone), cv.TimeZone=tz_sc; end
            end
            Tk.(col_time_sc)=cv;
            T_sc=[T_sc;Tk]; %#ok<AGROW>
        catch ME
            fprintf('  ERREUR %s : %s\n',fn_s{k},ME.message);
        end
    end
    if ~isempty(T_sc)
        if ~ismember(col_time_sc, T_sc.Properties.VariableNames)
            vars = T_sc.Properties.VariableNames;
            found = '';
            for vi=1:numel(vars)
                if isdatetime(T_sc.(vars{vi})), found=vars{vi}; break; end
            end
            if isempty(found)
                for vi=1:numel(vars)
                    if contains(lower(vars{vi}),'time')||contains(lower(vars{vi}),'date')
                        found=vars{vi}; break;
                    end
                end
            end
            if ~isempty(found)
                fprintf('  Colonne temps SCADA : "%s" (auto-detecte)\n',found);
                col_time_sc=found;
            end
        end
        T_sc=sortrows(T_sc,{col_time_sc},{'ascend'});
        t_sc=T_sc.(col_time_sc); t_sc.TimeZone='UTC';
        t0=min([t_utc(1);t_sc(1)]);
        xa=seconds(t_utc-t0); xs=seconds(t_sc-t0);
        isf=@(y,x) interp1(x(isfinite(y)&isfinite(x)),y(isfinite(y)&isfinite(x)),xa,'linear',NaN);
        if ismember(col_power,T_sc.Properties.VariableNames)
            power_ts=max(isf(double(T_sc.(col_power)),xs),0); has_pow=any(isfinite(power_ts));
            fprintf('  Puissance OK\n');
        end
        if ismember(col_speed,T_sc.Properties.VariableNames)
            wind_ts=max(isf(double(T_sc.(col_speed)),xs),0); has_wind=any(isfinite(wind_ts));
            fprintf('  Vent OK\n');
        end
        if ismember(col_rpm_sc,T_sc.Properties.VariableNames)
            rpm_sc=max(isf(double(T_sc.(col_rpm_sc)),xs),0); has_rpm_dir=any(isfinite(rpm_sc));
            fprintf('  RPM direct OK\n');
        end
    end
end

%% 5. SIGNAUX + FILTRAGE
ax_raw=double(T_acc.(col_ax)); ay_raw=double(T_acc.(col_ay));
iv=(1:N)';
ok=isfinite(ax_raw); if ~all(ok)&&sum(ok)>=2, ax_raw=interp1(iv(ok),ax_raw(ok),iv,'linear','extrap'); end
ok=isfinite(ay_raw); if ~all(ok)&&sum(ok)>=2, ay_raw=interp1(iv(ok),ay_raw(ok),iv,'linear','extrap'); end
fn_lo=f_lo/(fs/2); fn_hi=min(f_hi,fs/2*0.92)/(fs/2);
fn_lo=max(1e-4, min(fn_lo, 0.99));
fn_hi=max(fn_lo+1e-4, min(fn_hi, 0.999));
[b_bp,a_bp]=butter(1,[fn_lo fn_hi],'bandpass');
if any(abs(roots(a_bp))>=1), [b_bp,a_bp]=butter(1,fn_lo,'high'); end
ax_f=filtfilt(b_bp,a_bp,ax_raw); ay_f=filtfilt(b_bp,a_bp,ay_raw);
fprintf('  Filtrage [%.3f-%.3f Hz] OK\n',fn_lo*(fs/2),fn_hi*(fs/2));

% [CALCUL v5.5] Signaux filtrés bande résonance pour double intégration temporelle
% Filtre passe-bande ordre 2 sur la bande de résonance — utilisé pour D_amp(t)
fn_res_lo = f_res_lo/(fs/2);
fn_res_hi = min(f_res_hi, fs/2*0.95)/(fs/2);
fn_res_lo = max(1e-4, min(fn_res_lo, 0.99));
fn_res_hi = max(fn_res_lo+1e-4, min(fn_res_hi, 0.999));
[b_res, a_res] = butter(2, [fn_res_lo fn_res_hi], 'bandpass');
ax_res = filtfilt(b_res, a_res, ax_raw);
ay_res = filtfilt(b_res, a_res, ay_raw);

%% 6. RPM
rpm_est=nan(N,1); rpm_valid=false(N,1);
if has_rpm_dir
    rpm_est=rpm_sc; rpm_valid=isfinite(rpm_sc)&rpm_sc>2;
    fprintf('  RPM : SCADA direct\n');
elseif has_pow&&has_wind
    ok_b=isfinite(power_ts)&isfinite(wind_ts);
    z_rpm=ok_b&power_ts>seuil_kW&wind_ts>=v_z2_min&wind_ts<=v_z2_max&power_ts<P_zone2_max;
    rpm_est(z_rpm)=max(4,min(rpm_nom,rpm_nom*(power_ts(z_rpm)/P_nom_kW).^(1/3)));
    rpm_valid=z_rpm;
    fprintf('  RPM : estimation zones (%d pts)\n',sum(z_rpm));
end

%% 7. PSD GLISSANTE
win_s   = round(win_min*60*fs);
step_s  = round(win_s*(1-ovlp));
i0      = 1:step_s:(N-win_s+1);
nWin    = numel(i0);

nfft    = min(2^nextpow2(win_s), 2^17);
wham    = hamming(win_s);
% [CALCUL v5.5] Facteur de normalisation fenêtre corrigé
% wfac = sum(w^2)/N garantit que PSD*df integre = variance du signal
wfac_psd = sum(wham.^2) / win_s;   % normalisation PSD unilatérale correcte

fp = (0:nfft/2)/nfft*fs;
df = fp(2) - fp(1);   % résolution fréquentielle

% Masques fréquentiels
m_nb  = fp >= f_lo    & fp <= f_hi;
m_s   = fp >= f_s_lo  & fp <= f_s_hi;
m_bb  = fp >= f_bb_lo & fp <= f_bb_hi;
m_bf  = fp >= f_bf_lo & fp <= f_bf_hi;
m_res = fp >= f_res_lo & fp <= f_res_hi;

% [CALCUL v5.5] Pondération déplacement fréquence par fréquence
% PSD_déplacement(f) = PSD_acc(f) / (2*pi*f)^4   [m²/Hz -> m²/Hz]
% D_rms = sqrt(sum(PSD_acc(f)/(2*pi*f)^4 * df)) * 1000   [mm]
fp_safe       = max(fp, 1e-4);          % éviter /0 à f=0
w_disp_psd    = 1 ./ (2*pi*fp_safe).^4; % pondération PSD -> déplacement [m²/Hz]/(m/s²)²/Hz = m²·s⁴

% Poids par bande (vecteurs colonne, taille = sum(m_xx))
w_psd_res = w_disp_psd(m_res)';
w_psd_bf  = w_disp_psd(m_bf)';
w_psd_bb  = w_disp_psd(m_bb)';

t_win = NaT(nWin,1,'TimeZone',t_utc.TimeZone);
t_win.Format = t_utc.Format;

% KPI fréquence propre
kpi_f0ax = nan(nWin,1); kpi_f0ay = nan(nWin,1);
% KPI RMS accélération par bandes
kpi_rax   = nan(nWin,1); kpi_ray   = nan(nWin,1);
kpi_bb_ax = nan(nWin,1); kpi_bb_ay = nan(nWin,1);
kpi_bf_ax = nan(nWin,1); kpi_bf_ay = nan(nWin,1);
kpi_res_ax = nan(nWin,1); kpi_res_ay = nan(nWin,1);
% [CALCUL v5.5] KPI déplacement RMS intégration fréquentielle rigoureuse (mm)
kpi_disp_res_ax = nan(nWin,1); kpi_disp_res_ay = nan(nWin,1);
kpi_disp_bf_ax  = nan(nWin,1); kpi_disp_bf_ay  = nan(nWin,1);
kpi_disp_bb_ax  = nan(nWin,1); kpi_disp_bb_ay  = nan(nWin,1);
% KPI D_amp temporel (reconstruction par filtrage + double intégration)
kpi_damp_ax = nan(nWin,1); kpi_damp_ay = nan(nWin,1);
% KPI harmoniques rotor
kpi_1P_ax = nan(nWin,1); kpi_3P_ax = nan(nWin,1);
kpi_1P_ay = nan(nWin,1); kpi_3P_ay = nan(nWin,1);
% KPI contexte
kpi_pow  = nan(nWin,1); kpi_wind = nan(nWin,1);
kpi_rpm  = nan(nWin,1); rpm_v_win = false(nWin,1);

fprintf('  PSD glissante (%d fen.)...\n', nWin);
t0_psd = tic;

for k = 1:nWin
    idx = i0(k):(i0(k)+win_s-1);
    t_win(k) = t_utc(idx(round(end/2)));
    kpi_pow(k)  = mean(power_ts(idx),'omitnan');
    kpi_wind(k) = mean(wind_ts(idx),'omitnan');
    kpi_rpm(k)  = mean(rpm_est(idx),'omitnan');
    rpm_v_win(k) = mean(double(rpm_valid(idx)),'omitnan') > 0.5;
    rpm_k = kpi_rpm(k);

    for iax = 1:2
        if iax == 1
            sw     = ax_f(idx);
            sw_res = ax_res(idx);   % signal filtré bande résonance
        else
            sw     = ay_f(idx);
            sw_res = ay_res(idx);
        end

        % --- PSD normalisée ---
        seg = detrend(sw) .* wham;
        X   = fft(seg, nfft);
        % [CALCUL v5.5] Normalisation PSD unilatérale correcte :
        % PSD(f) = 2 * |X(f)|^2 / (fs * sum(w^2)) pour f>0
        %        = |X(0)|^2 / (fs * sum(w^2))      pour f=0
        % sum(w^2) = wfac_psd * win_s
        Pxx = 2 * abs(X(1:nfft/2+1)).^2 / (fs * sum(wham.^2));
        Pxx(1)   = Pxx(1) / 2;   % composante DC : pas de facteur 2
        Pxx(end) = Pxx(end) / 2; % Nyquist si nfft pair

        % --- RMS accélération par bandes ---
        rms_nb  = sqrt(max(0, sum(Pxx(m_nb )) * df));
        rms_bb  = sqrt(max(0, sum(Pxx(m_bb )) * df));
        rms_bf  = sqrt(max(0, sum(Pxx(m_bf )) * df));
        rms_res = sqrt(max(0, sum(Pxx(m_res)) * df));

        % [CALCUL v5.5] RMS déplacement par intégration fréquentielle rigoureuse
        % D_rms [m] = sqrt(sum(Pxx(f)/(2pi*f)^4 * df))  -> *1000 pour mm
        % Vecteurs de poids déjà extraits avant la boucle
        disp_rms_res = sqrt(max(0, sum(Pxx(m_res) .* w_psd_res) * df)) * 1000; % mm
        disp_rms_bf  = sqrt(max(0, sum(Pxx(m_bf)  .* w_psd_bf)  * df)) * 1000; % mm
        disp_rms_bb  = sqrt(max(0, sum(Pxx(m_bb)  .* w_psd_bb)  * df)) * 1000; % mm

        % [CALCUL v5.5] D_amp temporel : double intégration du signal filtré bande résonance
        % 1) Détrend du signal filtré
        % 2) Première intégration : vitesse [m/s]
        % 3) Correction dérive vitesse (hpf)
        % 4) Deuxième intégration : déplacement [m]
        % 5) Correction dérive déplacement (hpf)
        % Le P95 de l'amplitude absolue donne D_amp [mm]
        dt_s = 1/fs;
        seg_r = detrend(sw_res);
        vel   = cumtrapz(dt_s, seg_r);
        vel   = detrend(vel);   % correction dérive linéaire
        disp_t = cumtrapz(dt_s, vel);
        disp_t = detrend(disp_t);
        damp_p95 = prctile(abs(disp_t), 95) * 1000;   % mm

        % --- Harmoniques rotor ---
        rms_1P = NaN; rms_3P = NaN;
        if isfinite(rpm_k) && rpm_k > 1
            f1P = rpm_k/60;
            m1P = fp >= (f1P-harm_bw) & fp <= (f1P+harm_bw);
            m3P = fp >= (3*f1P-harm_bw) & fp <= (3*f1P+harm_bw);
            if any(m1P), rms_1P = sqrt(max(0, sum(Pxx(m1P)) * df)); end
            if any(m3P), rms_3P = sqrt(max(0, sum(Pxx(m3P)) * df)); end
        end

        % --- Détection f0 ---
        Ps = Pxx(m_s); fs_vec = fp(m_s);
        mh = true(size(fs_vec));
        if isfinite(rpm_k) && rpm_k > 1
            f1P = rpm_k/60;
            for h = 1:4, mh = mh & (abs(fs_vec - h*f1P) > harm_bw); end
        end
        Ps_c = Ps; Ps_c(~mh) = 0; f0_k = NaN;
        if any(Ps_c > 0)
            [pk_val, im] = max(Ps_c);
            fond_med = median(Ps_c(Ps_c > 0), 'omitnan');
            if pk_val > fond_med*4, f0_k = fs_vec(im); end
        end

        % --- Affectation KPI ---
        if iax == 1
            kpi_f0ax(k)       = f0_k;
            kpi_rax(k)        = rms_nb;
            kpi_bb_ax(k)      = rms_bb;
            kpi_bf_ax(k)      = rms_bf;
            kpi_res_ax(k)     = rms_res;
            kpi_disp_res_ax(k)= disp_rms_res;
            kpi_disp_bf_ax(k) = disp_rms_bf;
            kpi_disp_bb_ax(k) = disp_rms_bb;
            kpi_1P_ax(k)      = rms_1P;
            kpi_3P_ax(k)      = rms_3P;
            kpi_damp_ax(k)    = damp_p95;
        else
            kpi_f0ay(k)       = f0_k;
            kpi_ray(k)        = rms_nb;
            kpi_bb_ay(k)      = rms_bb;
            kpi_bf_ay(k)      = rms_bf;
            kpi_res_ay(k)     = rms_res;
            kpi_disp_res_ay(k)= disp_rms_res;
            kpi_disp_bf_ay(k) = disp_rms_bf;
            kpi_disp_bb_ay(k) = disp_rms_bb;
            kpi_1P_ay(k)      = rms_1P;
            kpi_3P_ay(k)      = rms_3P;
            kpi_damp_ay(k)    = damp_p95;
        end
    end
end

fprintf('  OK en %.0f s\n', toc(t0_psd));
fprintf('  Depl. RMS res max (integr. spectrale) : AX=%.4f mm | AY=%.4f mm\n', ...
    max(kpi_disp_res_ax,[],'omitnan'), max(kpi_disp_res_ay,[],'omitnan'));
fprintf('  Depl. amp temporel max : AX=%.4f mm | AY=%.4f mm\n', ...
    max(kpi_damp_ax,[],'omitnan'), max(kpi_damp_ay,[],'omitnan'));

%% 8. MASQUES EN MARCHE / STABLES
if has_pow&&any(kpi_pow>seuil_kW)
    mask_on=isfinite(kpi_pow)&kpi_pow>seuil_kW;
elseif has_wind
    mask_on=isfinite(kpi_wind)&kpi_wind>cutin_ms;
else
    rp20=prctile([kpi_rax(:);kpi_ray(:)],20);
    mask_on=kpi_rax>rp20|kpi_ray>rp20;
end
tr_m=diff([0;mask_on(:);0]); mask_trans=false(nWin,1);
for ii=find(tr_m==1)', mask_trans(max(1,ii-N_trans):min(nWin,ii+N_trans))=true; end
for ii=find(tr_m==-1)', mask_trans(max(1,ii-N_trans-1):min(nWin,ii+N_trans-1))=true; end
mask_st=mask_on&~mask_trans; n_st=sum(mask_st);
fprintf('  En marche:%d | Stables:%d / %d\n',sum(mask_on),n_st,nWin);

%% 9. REFERENCES
use_ext_ref    = false;
has_bf_res_ref = false;
has_disp_ref   = false;   % [BUG5] initialisé ici pour garantir son existence

% Initialisation des seuils déplacement (valeurs par défaut)
rep_disp_res_ax95 = NaN; rep_disp_res_ax99 = NaN;
rep_disp_res_ay95 = NaN; rep_disp_res_ay99 = NaN;

if isempty(ref_mat_file)
    [fn_ref, fp_ref] = uigetfile('REF_*.mat', ...
        'Charger une référence pré-calculée (Annuler = calcul interne)');
    if ~isequal(fn_ref, 0), ref_mat_file = fullfile(fp_ref, fn_ref); end
end

if ~isempty(ref_mat_file) && isfile(ref_mat_file)
    R = load(ref_mat_file);
    if isfield(R,'ref_meta') && ~strcmpi(R.ref_meta.turbine_id, turbine_id)
        warning('Référence pour %s, pas %s.', upper(R.ref_meta.turbine_id), upper(turbine_id));
    end
    f0_ref_ax  = R.f0_ref_ax; f0_ref_ay  = R.f0_ref_ay;
    p95_ax=R.p95_ax; p99_ax=R.p99_ax; p95_ay=R.p95_ay; p99_ay=R.p99_ay;
    p95_bb=R.p95_bb; p99_bb=R.p99_bb;
    rep_ax95=R.rep_ax95; rep_ax99=R.rep_ax99;
    rep_ay95=R.rep_ay95; rep_ay99=R.rep_ay99;
    rep_bb95=R.rep_bb95; rep_bb99=R.rep_bb99;
    vbins=R.vbins; nb=R.nb;
    has_bf_res_ref = isfield(R,'p95_bf_ax') && isfield(R,'p95_res_ax');
    if has_bf_res_ref
        p95_bf_ax=R.p95_bf_ax; p99_bf_ax=R.p99_bf_ax;
        p95_bf_ay=R.p95_bf_ay; p99_bf_ay=R.p99_bf_ay;
        p95_res_ax=R.p95_res_ax; p99_res_ax=R.p99_res_ax;
        p95_res_ay=R.p95_res_ay; p99_res_ay=R.p99_res_ay;
        rep_bf_ax95=R.rep_bf_ax95; rep_bf_ax99=R.rep_bf_ax99;
        rep_bf_ay95=R.rep_bf_ay95; rep_bf_ay99=R.rep_bf_ay99;
        rep_res_ax95=R.rep_res_ax95; rep_res_ax99=R.rep_res_ax99;
        rep_res_ay95=R.rep_res_ay95; rep_res_ay99=R.rep_res_ay99;
        if isfield(R,'ref_meta') && isfield(R.ref_meta,'f_bf_lo')
            f_bf_lo=R.ref_meta.f_bf_lo; f_bf_hi=R.ref_meta.f_bf_hi;
            f_res_lo=R.ref_meta.f_res_lo; f_res_hi=R.ref_meta.f_res_hi;
        end
        has_disp_ref = isfield(R,'rep_disp_res_ax95');
        if has_disp_ref
            p95_disp_res_ax=R.p95_disp_res_ax; p99_disp_res_ax=R.p99_disp_res_ax;
            p95_disp_res_ay=R.p95_disp_res_ay; p99_disp_res_ay=R.p99_disp_res_ay;
            p95_disp_bf_ax =R.p95_disp_bf_ax;  p99_disp_bf_ax =R.p99_disp_bf_ax;
            p95_disp_bf_ay =R.p95_disp_bf_ay;  p99_disp_bf_ay =R.p99_disp_bf_ay;
            rep_disp_res_ax95=R.rep_disp_res_ax95; rep_disp_res_ax99=R.rep_disp_res_ax99;
            rep_disp_res_ay95=R.rep_disp_res_ay95; rep_disp_res_ay99=R.rep_disp_res_ay99;
            rep_disp_bf_ax95 =R.rep_disp_bf_ax95;  rep_disp_bf_ax99 =R.rep_disp_bf_ax99;
            rep_disp_bf_ay95 =R.rep_disp_bf_ay95;  rep_disp_bf_ay99 =R.rep_disp_bf_ay99;
            if isfield(R,'ref_meta') && isfield(R.ref_meta,'H_tour'), H_tour=R.ref_meta.H_tour; end
        end
    end
    use_ext_ref = true;
    amp_thr_ax = prctile(kpi_rax(mask_st), 40);
    amp_thr_ay = prctile(kpi_ray(mask_st), 40);
    f0ax_fbl   = mask_st & isfinite(kpi_f0ax) & kpi_rax >= amp_thr_ax;
    f0ay_fbl   = mask_st & isfinite(kpi_f0ay) & kpi_ray >= amp_thr_ay;
    fprintf('  [REF EXTERNE] %s\n', ref_mat_file);
else
    fprintf('  [REF INTERNE] Calcul depuis les données courantes.\n');
    mask_ref = mask_st;
    amp_thr_ax = prctile(kpi_rax(mask_ref), 40);
    amp_thr_ay = prctile(kpi_ray(mask_ref), 40);
    f0ax_fbl   = mask_st & isfinite(kpi_f0ax) & kpi_rax >= amp_thr_ax;
    f0ay_fbl   = mask_st & isfinite(kpi_f0ay) & kpi_ray >= amp_thr_ay;
    mask_ref_f0 = mask_ref & f0ax_fbl & f0ay_fbl;
    if has_wind && any(isfinite(kpi_wind))
        mw = isfinite(kpi_wind) & kpi_wind > 5 & kpi_wind < 9;
        if sum(mask_ref_f0 & mw) >= 10, mask_ref_f0 = mask_ref_f0 & mw; end
    end
    f0_ref_ax = median(kpi_f0ax(mask_ref_f0), 'omitnan');
    f0_ref_ay = median(kpi_f0ay(mask_ref_f0), 'omitnan');
    fprintf('  f0 ref : AX=%.4f Hz | AY=%.4f Hz\n', f0_ref_ax, f0_ref_ay);
    vbins = [3 5 7 9 11 Inf]; nb = numel(vbins)-1;
end

f0_lo_ax = f0_ref_ax*(1-tol_f0); f0_hi_ax = f0_ref_ax*(1+tol_f0);
f0_lo_ay = f0_ref_ay*(1-tol_f0); f0_hi_ay = f0_ref_ay*(1+tol_f0);

%% 10. SEUILS P95/P99.5 PAR BIN DE VENT
if has_wind && any(isfinite(kpi_wind))
    bin_id = zeros(nWin,1);
    for ib = 1:nb
        if ib < nb
            m = isfinite(kpi_wind) & kpi_wind >= vbins(ib) & kpi_wind < vbins(ib+1);
        else
            m = isfinite(kpi_wind) & kpi_wind >= vbins(ib) & kpi_wind <= vbins(ib+1);
        end
        bin_id(m) = ib;
    end
else
    bin_id = ones(nWin,1);
    bin_id(~mask_st) = 0;
end

if ~exist('mask_ref','var') || isempty(mask_ref)
    mask_ref = mask_st;
end

p95_ax=nan(nb,1); p99_ax=nan(nb,1); p95_ay=nan(nb,1); p99_ay=nan(nb,1);
p95_bb=nan(nb,1); p99_bb=nan(nb,1);
p95_bf_ax=nan(nb,1); p99_bf_ax=nan(nb,1); p95_bf_ay=nan(nb,1); p99_bf_ay=nan(nb,1);
p95_res_ax=nan(nb,1); p99_res_ax=nan(nb,1); p95_res_ay=nan(nb,1); p99_res_ay=nan(nb,1);
% [CALCUL v5.5] Seuils déplacement par bin
p95_disp_res_ax=nan(nb,1); p99_disp_res_ax=nan(nb,1);
p95_disp_res_ay=nan(nb,1); p99_disp_res_ay=nan(nb,1);

for ib=1:nb
    mb = mask_ref & bin_id == ib;
    if sum(mb) >= 5
        p95_ax(ib)=prctile(kpi_rax(mb),95);       p99_ax(ib)=prctile(kpi_rax(mb),99.5);
        p95_ay(ib)=prctile(kpi_ray(mb),95);       p99_ay(ib)=prctile(kpi_ray(mb),99.5);
        p95_bb(ib)=prctile(kpi_bb_ax(mb),95);     p99_bb(ib)=prctile(kpi_bb_ax(mb),99.5);
        p95_bf_ax(ib)=prctile(kpi_bf_ax(mb),95);  p99_bf_ax(ib)=prctile(kpi_bf_ax(mb),99.5);
        p95_bf_ay(ib)=prctile(kpi_bf_ay(mb),95);  p99_bf_ay(ib)=prctile(kpi_bf_ay(mb),99.5);
        p95_res_ax(ib)=prctile(kpi_res_ax(mb),95);p99_res_ax(ib)=prctile(kpi_res_ax(mb),99.5);
        p95_res_ay(ib)=prctile(kpi_res_ay(mb),95);p99_res_ay(ib)=prctile(kpi_res_ay(mb),99.5);
        p95_disp_res_ax(ib)=prctile(kpi_disp_res_ax(mb),95);
        p99_disp_res_ax(ib)=prctile(kpi_disp_res_ax(mb),99.5);
        p95_disp_res_ay(ib)=prctile(kpi_disp_res_ay(mb),95);
        p99_disp_res_ay(ib)=prctile(kpi_disp_res_ay(mb),99.5);
    end
end

rep_ax95=nanmedian(p95_ax); rep_ax99=nanmedian(p99_ax);
rep_ay95=nanmedian(p95_ay); rep_ay99=nanmedian(p99_ay);
rep_bb95=nanmedian(p95_bb); rep_bb99=nanmedian(p99_bb);
rep_bf_ax95=nanmedian(p95_bf_ax); rep_bf_ax99=nanmedian(p99_bf_ax);
rep_bf_ay95=nanmedian(p95_bf_ay); rep_bf_ay99=nanmedian(p99_bf_ay);
rep_res_ax95=nanmedian(p95_res_ax); rep_res_ax99=nanmedian(p99_res_ax);
rep_res_ay95=nanmedian(p95_res_ay); rep_res_ay99=nanmedian(p99_res_ay);

% [BUG6] Seuils déplacement — garantis définis dans tous les cas
if ~has_disp_ref
    rep_disp_res_ax95 = nanmedian(p95_disp_res_ax);
    rep_disp_res_ax99 = nanmedian(p99_disp_res_ax);
    rep_disp_res_ay95 = nanmedian(p95_disp_res_ay);
    rep_disp_res_ay99 = nanmedian(p99_disp_res_ay);
    % Fallback si toujours NaN (données insuffisantes)
    if ~isfinite(rep_disp_res_ax95)
        rep_disp_res_ax95 = prctile(kpi_disp_res_ax(mask_st),95);
        rep_disp_res_ax99 = prctile(kpi_disp_res_ax(mask_st),99.5);
        rep_disp_res_ay95 = prctile(kpi_disp_res_ay(mask_st),95);
        rep_disp_res_ay99 = prctile(kpi_disp_res_ay(mask_st),99.5);
    end
end
fprintf('  Seuils depl. res P99.5 : AX=%.4f mm | AY=%.4f mm\n', ...
    rep_disp_res_ax99, rep_disp_res_ay99);

% Vecteurs seuils fenêtre par fenêtre
s_p95_ax=nan(nWin,1); s_p99_ax=nan(nWin,1);
s_p95_ay=nan(nWin,1); s_p99_ay=nan(nWin,1);
s_p95_bb=nan(nWin,1); s_p99_bb=nan(nWin,1);
s_p95_bf_ax=nan(nWin,1); s_p99_bf_ax=nan(nWin,1);
s_p95_bf_ay=nan(nWin,1); s_p99_bf_ay=nan(nWin,1);
s_p95_res_ax=nan(nWin,1); s_p99_res_ax=nan(nWin,1);
s_p95_res_ay=nan(nWin,1); s_p99_res_ay=nan(nWin,1);

for ib=1:nb
    mk=(bin_id==ib);
    if ~any(mk), continue; end
    if isfinite(p95_ax(ib)),     s_p95_ax(mk)=p95_ax(ib);     s_p99_ax(mk)=p99_ax(ib);     end
    if isfinite(p95_ay(ib)),     s_p95_ay(mk)=p95_ay(ib);     s_p99_ay(mk)=p99_ay(ib);     end
    if isfinite(p95_bb(ib)),     s_p95_bb(mk)=p95_bb(ib);     s_p99_bb(mk)=p99_bb(ib);     end
    if isfinite(p95_bf_ax(ib)),  s_p95_bf_ax(mk)=p95_bf_ax(ib); s_p99_bf_ax(mk)=p99_bf_ax(ib); end
    if isfinite(p95_bf_ay(ib)),  s_p95_bf_ay(mk)=p95_bf_ay(ib); s_p99_bf_ay(mk)=p99_bf_ay(ib); end
    if isfinite(p95_res_ax(ib)), s_p95_res_ax(mk)=p95_res_ax(ib); s_p99_res_ax(mk)=p99_res_ax(ib); end
    if isfinite(p95_res_ay(ib)), s_p95_res_ay(mk)=p95_res_ay(ib); s_p99_res_ay(mk)=p99_res_ay(ib); end
end
s_p95_ax(isnan(s_p95_ax)&bin_id>0)=rep_ax95;   s_p99_ax(isnan(s_p99_ax)&bin_id>0)=rep_ax99;
s_p95_ay(isnan(s_p95_ay)&bin_id>0)=rep_ay95;   s_p99_ay(isnan(s_p99_ay)&bin_id>0)=rep_ay99;
s_p95_bb(isnan(s_p95_bb)&bin_id>0)=rep_bb95;   s_p99_bb(isnan(s_p99_bb)&bin_id>0)=rep_bb99;
s_p95_bf_ax(isnan(s_p95_bf_ax)&bin_id>0)=rep_bf_ax95; s_p99_bf_ax(isnan(s_p99_bf_ax)&bin_id>0)=rep_bf_ax99;
s_p95_bf_ay(isnan(s_p95_bf_ay)&bin_id>0)=rep_bf_ay95; s_p99_bf_ay(isnan(s_p99_bf_ay)&bin_id>0)=rep_bf_ay99;
s_p95_res_ax(isnan(s_p95_res_ax)&bin_id>0)=rep_res_ax95; s_p99_res_ax(isnan(s_p99_res_ax)&bin_id>0)=rep_res_ax99;
s_p95_res_ay(isnan(s_p95_res_ay)&bin_id>0)=rep_res_ay95; s_p99_res_ay(isnan(s_p99_res_ay)&bin_id>0)=rep_res_ay99;

%% 11. EWMA
tmp_ax     = kpi_rax;    tmp_ax(~mask_st)    = NaN;
tmp_ay     = kpi_ray;    tmp_ay(~mask_st)    = NaN;
tmp_bb     = kpi_bb_ax;  tmp_bb(~mask_st)    = NaN;
tmp_bf_ax  = kpi_bf_ax;  tmp_bf_ax(~mask_st) = NaN;
tmp_bf_ay  = kpi_bf_ay;  tmp_bf_ay(~mask_st) = NaN;
tmp_res_ax = kpi_res_ax; tmp_res_ax(~mask_st)= NaN;
tmp_res_ay = kpi_res_ay; tmp_res_ay(~mask_st)= NaN;

ewma_ax    = movmean(tmp_ax,    ewma_win,'omitnan');
ewma_ay    = movmean(tmp_ay,    ewma_win,'omitnan');
ewma_bb    = movmean(tmp_bb,    ewma_win,'omitnan');
ewma_bf_ax = movmean(tmp_bf_ax, ewma_win,'omitnan');
ewma_bf_ay = movmean(tmp_bf_ay, ewma_win,'omitnan');
ewma_res_ax= movmean(tmp_res_ax,ewma_win,'omitnan');
ewma_res_ay= movmean(tmp_res_ay,ewma_win,'omitnan');

ewa_res_ax = isfinite(ewma_res_ax) & ewma_res_ax > s_p95_res_ax;
ewa_res_ay = isfinite(ewma_res_ay) & ewma_res_ay > s_p95_res_ay;
al_ewma_res_ax = ewa_res_ax & [false;ewa_res_ax(1:end-1)] & [false;false;ewa_res_ax(1:end-2)];
al_ewma_res_ay = ewa_res_ay & [false;ewa_res_ay(1:end-1)] & [false;false;ewa_res_ay(1:end-2)];

ewa_ax = isfinite(ewma_ax) & ewma_ax > s_p95_ax;
ewa_ay = isfinite(ewma_ay) & ewma_ay > s_p95_ay;
al_ewma_ax = ewa_ax & [false;ewa_ax(1:end-1)] & [false;false;ewa_ax(1:end-2)];
al_ewma_ay = ewa_ay & [false;ewa_ay(1:end-1)] & [false;false;ewa_ay(1:end-2)];

%% 12. ALERTES F0
cond_ctx = false(nWin,1);
if has_pow,    cond_ctx = cond_ctx | (isfinite(kpi_pow)  & kpi_pow  > p_min_ctx*P_nom_kW); end
if has_wind,   cond_ctx = cond_ctx | (isfinite(kpi_wind) & kpi_wind >= v_min_ctx); end
if has_rpm_dir || any(rpm_v_win), cond_ctx = cond_ctx | (isfinite(kpi_rpm) & kpi_rpm >= 5); end
if ~has_pow && ~has_wind, cond_ctx(:) = true; end

hb_ax = f0ax_fbl & mask_st & cond_ctx & (kpi_f0ax < f0_lo_ax | kpi_f0ax > f0_hi_ax);
hb_ay = f0ay_fbl & mask_st & cond_ctx & (kpi_f0ay < f0_lo_ay | kpi_f0ay > f0_hi_ay);
al_f0 = (hb_ax & hb_ay) | ...
        (hb_ax & [false;hb_ax(1:end-1)] & [false;false;hb_ax(1:end-2)]) | ...
        (hb_ay & [false;hb_ay(1:end-1)] & [false;false;hb_ay(1:end-2)]);

%% 13. ALERTES RMS + SCORE COMPOSITE
al_bf_ax = mask_st & isfinite(kpi_bf_ax) & kpi_bf_ax > s_p95_bf_ax;
al_bf_ay = mask_st & isfinite(kpi_bf_ay) & kpi_bf_ay > s_p95_bf_ay;
al_bf    = al_bf_ax | al_bf_ay;

al_res_ax = mask_st & isfinite(kpi_res_ax) & kpi_res_ax > s_p99_res_ax;
al_res_ay = mask_st & isfinite(kpi_res_ay) & kpi_res_ay > s_p99_res_ay;
al_res    = al_res_ax | al_res_ay | al_ewma_res_ax | al_ewma_res_ay;

info_res_ax = mask_st & isfinite(kpi_res_ax) & kpi_res_ax > s_p95_res_ax & ~al_res_ax;
info_res_ay = mask_st & isfinite(kpi_res_ay) & kpi_res_ay > s_p95_res_ay & ~al_res_ay;

al_rms_ax = mask_st & isfinite(kpi_rax) & kpi_rax > s_p99_ax;
al_rms_ay = mask_st & isfinite(kpi_ray) & kpi_ray > s_p99_ay;
al_rms    = al_rms_ax | al_rms_ay | al_ewma_ax | al_ewma_ay;
info_ax   = mask_st & isfinite(kpi_rax) & kpi_rax > s_p95_ax & ~al_rms_ax;
info_ay   = mask_st & isfinite(kpi_ray) & kpi_ray > s_p95_ay & ~al_rms_ay;

score = zeros(nWin,1);
score = score + double(al_bf) + double(al_res) + double(al_f0);
al_confirmed = mask_st & score >= 2;
al_warning   = mask_st & score == 1;

fprintf('  Alertes v5.5 : f0=%d | BF(P95)=%d | Res(P99.5)=%d | Score>=2=%d | Avert.=%d\n', ...
    sum(al_f0),sum(al_bf),sum(al_res),sum(al_confirmed),sum(al_warning));

%% 14. TENDANCE F0
t_days = days(t_win - t_win(1));
ok_ax = isfinite(kpi_f0ax) & f0ax_fbl & mask_st;
ok_ay = isfinite(kpi_f0ay) & f0ay_fbl & mask_st;
drift_ax = NaN; drift_ay = NaN; p_ax = [NaN NaN]; p_ay = [NaN NaN];
if sum(ok_ax) > 20, p_ax = polyfit(t_days(ok_ax), kpi_f0ax(ok_ax), 1); drift_ax = p_ax(1); end
if sum(ok_ay) > 20, p_ay = polyfit(t_days(ok_ay), kpi_f0ay(ok_ay), 1); drift_ay = p_ay(1); end
is_drft_ax = isfinite(drift_ax) && abs(drift_ax) > drift_alert;
is_drft_ay = isfinite(drift_ay) && abs(drift_ay) > drift_alert;
N_med = max(1, round(24*60/(win_min*(1-ovlp))));
v_ax = kpi_f0ax; v_ax(~f0ax_fbl) = NaN; trend_ax = movmedian(v_ax, N_med, 'omitnan');
v_ay = kpi_f0ay; v_ay(~f0ay_fbl) = NaN; trend_ay = movmedian(v_ay, N_med, 'omitnan');
fprintf('  Derive : AX=%.5f Hz/j | AY=%.5f Hz/j\n', drift_ax, drift_ay);

%% 15. COULEURS + ZONES ARRET
C_AX   = [0.10 0.40 0.85]; C_AY   = [0.05 0.55 0.20];
C_ALRT = [0.88 0.10 0.10]; C_WARN = [0.95 0.50 0.05];
C_OK   = [0.15 0.65 0.15]; C_STOP = [0.88 0.88 0.88];  % [BUG1] C_STOP (pas CSTOP)
C_DRFT = [0.55 0.00 0.55];

mask_arr = ~mask_on;
dm = diff([0;mask_arr(:);0]);
i_s = find(dm == 1); i_e = find(dm == -1) - 1;

t_wv = t_win(~isnat(t_win));
if ~isempty(t_wv)
    xl = [t_wv(1)-minutes(30), t_wv(end)+minutes(30)];
else
    xl = [datetime('now','TimeZone','UTC')-hours(1), datetime('now','TimeZone','UTC')+hours(1)];
end
t_total_h = nWin * win_min * (1-ovlp) / 60;

%% 16. OMA FDD (CALCUL)
fprintf('\n=== OMA FDD (calcul) ===\n');
f0_fdd=NaN; damp_fdd=NaN; f1_hp=NaN; f2_hp=NaN; n_used=0;

win_fdd  = min(round(fdd_seg_min*60*fs), N);
nfft_fdd = min(2^nextpow2(win_fdd), 2^17);
step_fdd = round(win_fdd*(1-fdd_ovlp));
i0_fdd   = 1:step_fdd:(N-win_fdd+1);
nW_fdd   = numel(i0_fdd);
fp_fdd   = (0:nfft_fdd/2)/nfft_fdd*fs;
wfdd     = hamming(win_fdd);
nF_fdd   = nfft_fdd/2+1;

Sxx=zeros(nF_fdd,1); Syy=zeros(nF_fdd,1); Sxy=zeros(nF_fdd,1,'like',1+1i);

fprintf('  %d segments...', nW_fdd);
for k = 1:nW_fdd
    idx    = i0_fdd(k):(i0_fdd(k)+win_fdd-1);
    t_mid  = t_utc(idx(round(end/2)));
    [~,km] = min(abs(seconds(t_win-t_mid)));
    if ~mask_on(km), continue; end
    segx = detrend(ax_f(idx)).*wfdd; segy = detrend(ay_f(idx)).*wfdd;
    Fx=fft(segx,nfft_fdd)/nfft_fdd; Fy=fft(segy,nfft_fdd)/nfft_fdd;
    Fx=Fx(1:nF_fdd); Fy=Fy(1:nF_fdd);
    Sxx=Sxx+real(Fx.*conj(Fx)); Syy=Syy+real(Fy.*conj(Fy));
    Sxy=Sxy+Fx.*conj(Fy); n_used=n_used+1;
end
Sxx=Sxx/max(n_used,1); Syy=Syy/max(n_used,1); Sxy=Sxy/max(n_used,1);
fprintf(' %d segments en marche\n', n_used);

s1=zeros(nF_fdd,1); s2=zeros(nF_fdd,1);
for f_idx=1:nF_fdd
    G=[Sxx(f_idx) Sxy(f_idx);conj(Sxy(f_idx)) Syy(f_idx)];
    sv=svd(G); s1(f_idx)=sv(1); s2(f_idx)=sv(2);
end
coh = abs(Sxy).^2 ./ max(Sxx.*Syy,eps);

fm_fdd = fp_fdd>=fdd_f_lo & fp_fdd<=fdd_f_hi;
fp_d   = fp_fdd(fm_fdd);
s1_dB  = 10*log10(s1(fm_fdd)+eps); s2_dB = 10*log10(s2(fm_fdd)+eps);
coh_d  = coh(fm_fdd);
s1_sm_dB  = smoothdata(s1_dB,'gaussian',9);
s1_lin    = 10.^(s1_dB/10); s1_sm_lin = 10.^(s1_sm_dB/10);

% Estimation RPM robuste
rpm_min_phys=5; rpm_max_phys=22; rpm_med=NaN;
if has_rpm_dir && any(rpm_v_win&mask_on)
    vals=kpi_rpm(rpm_v_win&mask_on); vals=vals(vals>=rpm_min_phys&vals<=rpm_max_phys);
    if numel(vals)>=3, rpm_med=median(vals,'omitnan'); end
end
if ~isfinite(rpm_med)
    vals=kpi_rpm(mask_st&isfinite(kpi_rpm)); vals=vals(vals>=rpm_min_phys&vals<=rpm_max_phys);
    if numel(vals)>=3, rpm_med=median(vals,'omitnan'); end
end
if ~isfinite(rpm_med)
    vals=kpi_rpm(mask_on&isfinite(kpi_rpm)); vals=vals(vals>=rpm_min_phys&vals<=rpm_max_phys);
    if numel(vals)>=1, rpm_med=median(vals,'omitnan'); end
end
if ~isfinite(rpm_med)
    f3P_lo=rpm_min_phys*3/60; f3P_hi=rpm_max_phys*3/60;
    m3P_z=fp_d>=f3P_lo&fp_d<=f3P_hi&(fp_d<f_s_lo|fp_d>f_s_hi);
    if any(m3P_z)
        [pk3,i3]=max(s1_sm_dB(m3P_z)); bg3=median(s1_sm_dB(m3P_z),'omitnan');
        if pk3>bg3+3, f3P_vec=fp_d(m3P_z); rpm_med=f3P_vec(i3)/3*60; end
    end
end

% Détection f0 FDD
band_s_fdd=fp_d>=f_s_lo&fp_d<=f_s_hi;
if isfinite(rpm_med)
    f1P_med=rpm_med/60;
    for h=1:8, band_s_fdd=band_s_fdd&(abs(fp_d-h*f1P_med)>harm_bw); end
end
if sum(band_s_fdd)<5, band_s_fdd=fp_d>=f_s_lo&fp_d<=f_s_hi; end

if any(band_s_fdd)
    s1_search=s1_lin; s1_search(~band_s_fdd)=0;
    [~,ip_g]=max(s1_search); f0_fdd=fp_d(ip_g);
    if ip_g>1&&ip_g<numel(fp_d)
        i_lo=max(1,ip_g-3); i_hi=min(numel(fp_d),ip_g+3);
        [~,im_loc]=max(s1_lin(i_lo:i_hi)); ip_g2=i_lo+im_loc-1;
        if ip_g2>1&&ip_g2<numel(fp_d)
            y3=s1_lin(ip_g2-1:ip_g2+1); x3=fp_d(ip_g2-1:ip_g2+1); dx=x3(2)-x3(1);
            denom=2*(y3(1)-2*y3(2)+y3(3));
            if abs(denom)>eps*max(abs(y3))
                f0_cand=x3(2)-(y3(3)-y3(1))/denom*dx;
                if f0_cand>=f_s_lo&&f0_cand<=f_s_hi&&abs(f0_cand-f0_fdd)<4*dx
                    f0_fdd=f0_cand;
                end
            end
        end
    end
    [~,ip_sm]=min(abs(fp_d-f0_fdd));
    pk_val=s1_sm_lin(ip_sm); hp_lev=pk_val*hp_bw_frac^2;
    left=1:ip_sm-1; right=ip_sm+1:numel(fp_d);
    if ~isempty(left), il=find(s1_sm_lin(left)<=hp_lev,1,'last'); if ~isempty(il), f1_hp=fp_d(left(il)); end; end
    if ~isempty(right), ir=find(s1_sm_lin(right)<=hp_lev,1,'first'); if ~isempty(ir), f2_hp=fp_d(right(ir)); end; end
    if isfinite(f1_hp)&&isfinite(f2_hp)&&f0_fdd>0
        damp_fdd=(f2_hp-f1_hp)/(2*f0_fdd)*100;
        if damp_fdd>8, fprintf('  FDD : amortissement %.2f%% suspect\n',damp_fdd); end
    end
end
fprintf('  FDD : f0=%.4f Hz | amortissement=%.2f%%\n', f0_fdd, damp_fdd);

%% FIG 1 - DASHBOARD SANTE (3 panneaux, simplifie)
% Garde : f0 + RMS resonance + spectrogramme PSD
% Supprime : deplacement RMS dashboard + indice de confiance + alertes visuelles

fig1 = figure('Name', sprintf('[%s] Dashboard Sante', upper(turbine_id)), 'Color', 'w');
set(fig1, 'Position', [20 20 1540 920]);

% ---------- Panneau 1 : f0 ----------
sp1 = subplot(3,1,1); hold on;
yl_f0 = [f_s_lo*0.93 f_s_hi*1.07];

for ii = 1:numel(i_s)
    x1 = t_win(i_s(ii)); x2 = t_win(min(i_e(ii)+1, nWin));
    fill([x1 x2 x2 x1], [yl_f0(1) yl_f0(1) yl_f0(2) yl_f0(2)], ...
        C_STOP, 'EdgeColor', 'none', 'FaceAlpha', 0.40, 'HandleVisibility', 'off');
end

scatter(t_win(f0ax_fbl), kpi_f0ax(f0ax_fbl), 20, C_AX, 'filled', ...
    'MarkerFaceAlpha', 0.45, 'DisplayName', 'f0 AX');
scatter(t_win(f0ay_fbl), kpi_f0ay(f0ay_fbl), 20, C_AY, 'filled', ...
    'MarkerFaceAlpha', 0.45, 'DisplayName', 'f0 AY');

plot(t_win, trend_ax, '-', 'Color', C_AX, 'LineWidth', 2, 'DisplayName', 'Tend. AX');
plot(t_win, trend_ay, '-', 'Color', C_AY, 'LineWidth', 2, 'DisplayName', 'Tend. AY');

patch([t_win(1) t_win(end) t_win(end) t_win(1)], [f0_lo_ax f0_lo_ax f0_hi_ax f0_hi_ax], ...
    C_AX, 'FaceAlpha', 0.06, 'EdgeColor', 'none', 'HandleVisibility', 'off');
patch([t_win(1) t_win(end) t_win(end) t_win(1)], [f0_lo_ay f0_lo_ay f0_hi_ay f0_hi_ay], ...
    C_AY, 'FaceAlpha', 0.06, 'EdgeColor', 'none', 'HandleVisibility', 'off');

yline(f0_ref_ax, '--', 'Color', C_AX, 'LineWidth', 1.2, 'DisplayName', 'Ref AX');
yline(f0_ref_ay, '--', 'Color', C_AY, 'LineWidth', 1.2, 'DisplayName', 'Ref AY');

if sum(ok_ax) > 20
    cc_ax = C_DRFT*is_drft_ax + [0.65 0.65 0.65]*(~is_drft_ax);
    plot(t_win(1)+days(t_days(ok_ax)), polyval(p_ax, t_days(ok_ax)), '--', ...
        'Color', cc_ax, 'LineWidth', 1.2, ...
        'DisplayName', sprintf('Regr. AX %.5f Hz/j', drift_ax));
end
if sum(ok_ay) > 20
    cc_ay = C_DRFT*is_drft_ay + [0.65 0.65 0.65]*(~is_drft_ay);
    plot(t_win(1)+days(t_days(ok_ay)), polyval(p_ay, t_days(ok_ay)), '--', ...
        'Color', cc_ay, 'LineWidth', 1.2, ...
        'DisplayName', sprintf('Regr. AY %.5f Hz/j', drift_ay));
end

drft_lbl = sprintf('AX: %.5f Hz/j | AY: %.5f Hz/j', drift_ax, drift_ay);
text(0.99, 0.05, drft_lbl, 'Units', 'normalized', 'FontSize', 13, ...
    'BackgroundColor', 'w', 'EdgeColor', [0.7 0.7 0.7], ...
    'HorizontalAlignment', 'right');

grid on; ylim(yl_f0);
ylabel('f_0 (Hz)', 'FontSize', 14);
title(sprintf('[%s] Frequence propre f0 | ref AX=%.4f Hz  AY=%.4f Hz | tol +/-%.0f%%', ...
    upper(turbine_id), f0_ref_ax, f0_ref_ay, tol_f0*100), 'FontSize', 14);
legend('Location', 'best', 'FontSize', 12, 'NumColumns', 4);
xtickformat('dd/MM/yy'); xtickangle(10);
set(gca, 'XLim', xl, 'FontSize', 14);

% ---------- Panneau 2 : RMS resonance, P95 uniquement ----------
sp2 = subplot(3,1,2); hold on;
yl_r = [0, 1.25*max([nanmax(kpi_res_ax(mask_st)); nanmax(kpi_res_ay(mask_st)); ...
                     rep_res_ax95; rep_res_ay95; eps])];

for ii = 1:numel(i_s)
    x1 = t_win(i_s(ii)); x2 = t_win(min(i_e(ii)+1, nWin));
    fill([x1 x2 x2 x1], [yl_r(1) yl_r(1) yl_r(2) yl_r(2)], ...
        C_STOP, 'EdgeColor', 'none', 'FaceAlpha', 0.40, 'HandleVisibility', 'off');
end

scatter(t_win(mask_st), kpi_bf_ax(mask_st), 8, [0.78 0.78 0.78], 'filled', ...
    'MarkerFaceAlpha', 0.20, 'DisplayName', sprintf('BF AX [%.2f-%.2fHz]', f_bf_lo, f_bf_hi));
scatter(t_win(mask_st), kpi_bf_ay(mask_st), 8, [0.60 0.80 0.60], 'filled', ...
    'MarkerFaceAlpha', 0.20, 'DisplayName', sprintf('BF AY [%.2f-%.2fHz]', f_bf_lo, f_bf_hi));

scatter(t_win(mask_st), kpi_res_ax(mask_st), 10, C_AX, 'filled', ...
    'MarkerFaceAlpha', 0.45, 'DisplayName', sprintf('Res AX [%.2f-%.2fHz]', f_res_lo, f_res_hi));
scatter(t_win(mask_st), kpi_res_ay(mask_st), 10, C_AY, 'filled', ...
    'MarkerFaceAlpha', 0.45, 'DisplayName', sprintf('Res AY [%.2f-%.2fHz]', f_res_lo, f_res_hi));

plot(t_win, ewma_res_ax, '-', 'Color', C_AX, 'LineWidth', 1.6, 'DisplayName', 'EWMA AX');
plot(t_win, ewma_res_ay, '-', 'Color', C_AY, 'LineWidth', 1.6, 'DisplayName', 'EWMA AY');

yline(rep_res_ax95, '--', 'Color', C_WARN, 'LineWidth', 1.2, ...
    'Label', 'P95 ref', 'LabelHorizontalAlignment', 'left', 'DisplayName', 'P95 ref AX');
yline(rep_res_ay95, ':', 'Color', C_WARN*0.85, 'LineWidth', 1.2, ...
    'Label', 'P95 ref', 'LabelHorizontalAlignment', 'left', 'DisplayName', 'P95 ref AY');

grid on; ylim(yl_r);
ylabel('RMS acc (m/s^2)', 'FontSize', 14);
title(sprintf('RMS resonance [%.2f-%.2f Hz] | P95 ref uniquement', f_res_lo, f_res_hi), 'FontSize', 14);
legend('Location', 'best', 'FontSize', 12, 'NumColumns', 4);
xtickformat('dd/MM/yy'); xtickangle(10);
set(gca, 'XLim', xl, 'FontSize', 14);

% ---------- Panneau 3 : spectrogramme / PSD glissante ----------
% Recalcule un spectrogramme simple AX a partir des memes fenetres PSD
PAX_mat = nan(sum(m_bb), nWin);

for k = 1:nWin
    idx = i0(k):(i0(k)+win_s-1);
    seg = detrend(ax_f(idx)) .* wham;
    X   = fft(seg, nfft);
    Pxx = 2 * abs(X(1:nfft/2+1)).^2 / (fs * sum(wham.^2));
    Pxx(1)   = Pxx(1) / 2;
    Pxx(end) = Pxx(end) / 2;
    PAX_mat(:,k) = Pxx(m_bb);
end

sp3 = subplot(3,1,3); hold on;

t_num = datenum(t_win(:));
imagesc(t_num, fp(m_bb), 10*log10(PAX_mat + eps));
set(gca, 'YDir', 'normal');
colormap(sp3, turbo);
cb = colorbar;
cb.Label.String = 'PSD AX (dB)';

ylabel('Frequence (Hz)', 'FontSize', 14);
xlabel('Temps UTC', 'FontSize', 14);
title(sprintf('Spectrogramme PSD AX [%.2f-%.2f Hz]', f_bb_lo, f_bb_hi), 'FontSize', 14);
ylim([f_bb_lo f_bb_hi]);
xlim(datenum(xl));
datetick('x', 'dd/mm/yy', 'keeplimits');
xtickangle(10);
set(gca, 'FontSize', 14);
grid on;

sgtitle(sprintf('[%s] Dashboard Sante simplifie | %.0f h | %d fenetres', ...
    upper(turbine_id), t_total_h, nWin), ...
    'FontSize', 15, 'FontWeight', 'bold');
%% 16b. VALIDATION CROISEE FDD vs REFERENCE
df_fdd_ax=abs(f0_fdd-f0_ref_ax); df_fdd_ay=abs(f0_fdd-f0_ref_ay);
fprintf('  Ecart FDD vs ref : AX=%.4f Hz | AY=%.4f Hz\n',df_fdd_ax,df_fdd_ay);
fdd_match_ax=isfinite(df_fdd_ax)&&df_fdd_ax<=tol_f0*f0_ref_ax;
fdd_match_ay=isfinite(df_fdd_ay)&&df_fdd_ay<=tol_f0*f0_ref_ay;

%% FIG 2 - MODAL DIAGRAM
fprintf('\n=== Modal Diagram ===\n');
win_md=min(3600*fs,N); nfft_md=min(2^nextpow2(win_md),2^17);
step_md=round(win_md*0.50); i0_md=1:step_md:(N-win_md+1); nW_md=numel(i0_md);
fp_md=(0:nfft_md/2)/nfft_md*fs; wmd=hamming(win_md);
nsub=min(300,nW_md); idx_md=round(linspace(1,nW_md,nsub));
fm_md=fp_md>=0.10&fp_md<=0.80; fp_md_s=fp_md(fm_md);
Xax_all=zeros(nsub,sum(fm_md)); Xay_all=zeros(nsub,sum(fm_md)); all_db=[];
fprintf('  Calcul %d fenetres...',nsub);
for ki=1:nsub
    kh=idx_md(ki); idx=i0_md(kh):i0_md(kh)+win_md-1;
    Ax=20*log10(abs(fft(detrend(ax_f(idx)).*wmd,nfft_md))/nfft_md*2+eps)';
    Ay=20*log10(abs(fft(detrend(ay_f(idx)).*wmd,nfft_md))/nfft_md*2+eps)';
    Xax_all(ki,:)=Ax(fm_md)'; Xay_all(ki,:)=Ay(fm_md)';
    all_db=[all_db;Ax(fm_md)]; %#ok<AGROW>
end
fprintf(' OK\n');
thr_md=prctile(all_db,65);
nbin_env=300; f_env_edges=linspace(min(fp_md_s),max(fp_md_s),nbin_env+1);
f_env_c=f_env_edges(1:end-1)+diff(f_env_edges)/2;
env_lo_ax=nan(nbin_env,1); env_hi_ax=nan(nbin_env,1); env_med_ax=nan(nbin_env,1);
env_lo_ay=nan(nbin_env,1); env_hi_ay=nan(nbin_env,1); env_med_ay=nan(nbin_env,1);
for ib=1:nbin_env
    mi=fp_md_s>=f_env_edges(ib)&fp_md_s<f_env_edges(ib+1);
    if any(mi)
        vax=Xax_all(:,mi(:)'); vay=Xay_all(:,mi(:)');
        env_lo_ax(ib)=prctile(vax(:),10); env_hi_ax(ib)=prctile(vax(:),90); env_med_ax(ib)=prctile(vax(:),50);
        env_lo_ay(ib)=prctile(vay(:),10); env_hi_ay(ib)=prctile(vay(:),90); env_med_ay(ib)=prctile(vay(:),50);
    end
end
sm_win=max(5,round(nbin_env/60));
env_hi_ax_sm=smoothdata(env_hi_ax,'gaussian',sm_win);
env_hi_ay_sm=smoothdata(env_hi_ay,'gaussian',sm_win);
[f0_md_ax,zeta_md_ax,f1_ax,f2_ax]=env_modal_params(f_env_c,env_hi_ax_sm,f_s_lo,f_s_hi,hp_bw_frac);
[f0_md_ay,zeta_md_ay,f1_ay,f2_ay]=env_modal_params(f_env_c,env_hi_ay_sm,f_s_lo,f_s_hi,hp_bw_frac);
fprintf('  Enveloppe P90 : AX f0=%.4fHz zeta=%.2f%% | AY f0=%.4fHz zeta=%.2f%%\n',f0_md_ax,zeta_md_ax,f0_md_ay,zeta_md_ay);

fig2=figure('Name',sprintf('[%s] Modal Diagram',upper(turbine_id)),'Color','w');
set(fig2,'Position',[40 40 1440 760]);
for iax=1:2
    sp_md=subplot(2,1,iax); hold on;
    if iax==1, Xall=Xax_all; env_lo=env_lo_ax; env_hi_s=env_hi_ax_sm; env_med=env_med_ax; f0_e=f0_md_ax; ze_e=zeta_md_ax; f1_e=f1_ax; f2_e=f2_ax; C_t=C_AX; lbl='AX (fore-aft)';
    else,      Xall=Xay_all; env_lo=env_lo_ay; env_hi_s=env_hi_ay_sm; env_med=env_med_ay; f0_e=f0_md_ay; ze_e=zeta_md_ay; f1_e=f1_ay; f2_e=f2_ay; C_t=C_AY; lbl='AY (lateral)'; end
    all_f=[]; all_a=[];
    for ki=1:nsub
        row=Xall(ki,:); show=row>thr_md;
        if any(show), all_f=[all_f,fp_md_s(show)]; all_a=[all_a,row(show)]; end %#ok<AGROW>
    end
    if numel(all_f)>=2, scatter(all_f(:),all_a(:),4,all_a(:),'filled','MarkerFaceAlpha',0.5,'MarkerEdgeAlpha',0,'DisplayName','FFT scatter'); end
    colormap(sp_md,jet);
    % [BUG4] colorbar créée une seule fois ici, pas dans une sous-boucle imbriquée
    cb=colorbar; cb.Label.String='dB'; cb.FontSize=13;
    clim_lo=double(thr_md(1)); clim_hi=double(max(all_a(:)));
    if isfinite(clim_lo)&&isfinite(clim_hi)&&clim_hi>clim_lo, clim([clim_lo clim_hi]); end
    plot(f_env_c,env_lo,'-','Color',[0.9 0.9 0.9],'LineWidth',0.8,'DisplayName','P10 (plancher)');
    plot(f_env_c,env_med,'-','Color',[1.0 1.0 0.6],'LineWidth',1.0,'DisplayName','P50 (mediane)');
    plot(f_env_c,env_hi_s,'--','Color','w','LineWidth',1.5,'DisplayName','P90 lisse (estimateur)');
    yl_md=ylim; x_box=min(fp_md_s)+0.03*(max(fp_md_s)-min(fp_md_s));
    if isfinite(f0_e)
        xline(f0_e,'-','Color',C_t,'LineWidth',2,'HandleVisibility','off');
        if isfinite(f1_e), xline(f1_e,':','Color',C_t,'LineWidth',1,'HandleVisibility','off'); end
        if isfinite(f2_e), xline(f2_e,':','Color',C_t,'LineWidth',1,'HandleVisibility','off'); end
        if isfinite(ze_e), txt_env=sprintf('P90: f0=%.4fHz  zeta=%.2f%%',f0_e,ze_e);
        else, txt_env=sprintf('P90: f0=%.4fHz  zeta=N/A',f0_e); end
        text(x_box,yl_md(1)+0.18*diff(yl_md),txt_env,'FontSize',14,'Color',C_t,'FontWeight','bold','BackgroundColor','w','EdgeColor',C_t,'VerticalAlignment','bottom','HorizontalAlignment','left');
    end
    if isfinite(f0_fdd)
        xline(f0_fdd,'--r','LineWidth',1.5,'HandleVisibility','off');
        if isfinite(damp_fdd), txt_fdd=sprintf('FDD: f0=%.4fHz  zeta=%.2f%%',f0_fdd,damp_fdd);
        else, txt_fdd=sprintf('FDD: f0=%.4fHz  zeta=N/A',f0_fdd); end
        text(x_box,yl_md(1)+0.08*diff(yl_md),txt_fdd,'FontSize',14,'Color',[0.85 0.05 0.05],'FontWeight','bold','BackgroundColor','w','EdgeColor',[0.85 0.05 0.05],'VerticalAlignment','bottom','HorizontalAlignment','left');
    end
    grid on; xlabel('Frequence (Hz)','FontSize',14); ylabel('Amplitude (dB)','FontSize',14);
    title(sprintf('[%s] Modal Diagram %s | %d fen. | P90: f0=%.4fHz | FDD: zeta=%.2f%%',upper(turbine_id),lbl,nsub,f0_e,damp_fdd),'FontSize',14);
    legend('Location','northwest','FontSize',14,'NumColumns',2);
end
sgtitle(sprintf('[%s] Modal Diagram | AX P90: %.4fHz | AY P90: %.4fHz | FDD: f0=%.4fHz zeta=%.2f%%',upper(turbine_id),f0_md_ax,f0_md_ay,f0_fdd,damp_fdd),'FontSize',14,'FontWeight','bold');
annotation(fig2,'textbox',[0.78 0.01 0.20 0.05],'String',sprintf('Match ref: AX=%s | AY=%s',yesno(fdd_match_ax),yesno(fdd_match_ay)),'FitBoxToText','on','BackgroundColor','w','EdgeColor',[0.7 0.7 0.7]);

%% FIG 3 - OMA FDD (TRACE)
fprintf('\n=== OMA FDD (figure) ===\n');
fig3=figure('Name',sprintf('[%s] OMA FDD',upper(turbine_id)),'Color','w');
set(fig3,'Position',[60 60 1200 780]);
sp_f1=subplot(3,1,1); hold on;
plot(fp_d,s1_dB,'-','Color',C_AX,'LineWidth',1.6,'DisplayName','s1 (mode dominant)');
plot(fp_d,s2_dB,'-','Color',[0.65 0.65 0.65],'LineWidth',0.9,'DisplayName','s2');
yl_f1=ylim;
fill([f_s_lo f_s_hi f_s_hi f_s_lo],[yl_f1(1) yl_f1(1) yl_f1(2) yl_f1(2)],C_AX,'FaceAlpha',0.06,'EdgeColor','none','HandleVisibility','off');
if isfinite(f0_fdd)
    xline(f0_fdd,'--r','LineWidth',1.3,'HandleVisibility','off');
    if isfinite(damp_fdd), txt_fdd=sprintf('f0 = %.4f Hz\nAmort. = %.2f %%',f0_fdd,damp_fdd);
    else, txt_fdd=sprintf('f0 = %.4f Hz\nAmort. = N/A',f0_fdd); end
    text(0.02,0.95,txt_fdd,'Units','normalized','FontSize',13,'Color','r','FontWeight','bold','BackgroundColor','w','EdgeColor','r','VerticalAlignment','top');
    if isfinite(f1_hp), xline(f1_hp,':r','LineWidth',0.9,'HandleVisibility','off'); end
    if isfinite(f2_hp), xline(f2_hp,':r','LineWidth',0.9,'HandleVisibility','off'); end
end
grid on; xlabel('Frequence (Hz)'); ylabel('Amplitude (dB)');
title(sprintf('[%s] FDD - Valeurs singulieres | %d segments %.0f min',upper(turbine_id),n_used,fdd_seg_min));
legend('Location','best','FontSize',13); xlim([fdd_f_lo fdd_f_hi]);

sp_f2=subplot(3,1,2); hold on;
plot(fp_d,coh_d,'-','Color',[0.20 0.50 0.80],'LineWidth',1.3,'DisplayName','Coherence AX-AY');
yline(0.8,'--r','LineWidth',1,'DisplayName','Seuil 0.8');
yline(0.6,':','Color',[0.8 0.5 0],'LineWidth',0.9,'DisplayName','Seuil 0.6');
if isfinite(f0_fdd), xline(f0_fdd,'--r','LineWidth',1.0,'HandleVisibility','off'); end
fill([f_s_lo f_s_hi f_s_hi f_s_lo],[0 0 1 1],C_AX,'FaceAlpha',0.06,'EdgeColor','none','HandleVisibility','off');
grid on; ylim([0 1]); xlabel('Frequence (Hz)'); ylabel('Coherence');
title('Coherence AX-AY'); legend('Location','best','FontSize',13); xlim([fdd_f_lo fdd_f_hi]);

sp_f3=subplot(3,1,3); hold on;
plot(fp_d,10*log10(Sxx(fm_fdd)+eps),'-','Color',C_AX,'LineWidth',1.2,'DisplayName','PSD AX');
plot(fp_d,10*log10(Syy(fm_fdd)+eps),'-','Color',C_AY,'LineWidth',1.2,'DisplayName','PSD AY');
if isfinite(f0_fdd), xline(f0_fdd,'--r','LineWidth',1.0,'HandleVisibility','off'); end
yl_f3=ylim;
fill([f_s_lo f_s_hi f_s_hi f_s_lo],[yl_f3(1) yl_f3(1) yl_f3(2) yl_f3(2)],C_AX,'FaceAlpha',0.06,'EdgeColor','none','HandleVisibility','off');
grid on; xlabel('Frequence (Hz)'); ylabel('PSD (dB)');
title('PSD AX et AY'); legend('Location','best','FontSize',13); xlim([fdd_f_lo fdd_f_hi]);
if isfinite(damp_fdd), sgtitle(sprintf('[%s] OMA FDD | f0=%.4fHz | Amort=%.2f%% | %d segments',upper(turbine_id),f0_fdd,damp_fdd,n_used),'FontSize',14,'FontWeight','bold');
else, sgtitle(sprintf('[%s] OMA FDD | f0=%.4fHz | Amort=N/A | %d segments',upper(turbine_id),f0_fdd,n_used),'FontSize',14,'FontWeight','bold'); end

%% FIG 4 - DEPLACEMENT AMPLITUDE TEMPOREL (simplifie)
fprintf('=== Fig 4 - Deplacement amplitude temporel ===\n');

tmp_damp_ax = kpi_damp_ax; tmp_damp_ax(~mask_st) = NaN;
tmp_damp_ay = kpi_damp_ay; tmp_damp_ay(~mask_st) = NaN;
ewma_damp_ax = movmean(tmp_damp_ax, ewma_win, 'omitnan');
ewma_damp_ay = movmean(tmp_damp_ay, ewma_win, 'omitnan');

damp_ref_ax95 = prctile(kpi_damp_ax(mask_st), 95);
damp_ref_ay95 = prctile(kpi_damp_ay(mask_st), 95);

fig4 = figure('Name', sprintf('[%s] Deplacement Amplitude', upper(turbine_id)), 'Color', 'w');
set(fig4, 'Position', [80 80 1400 520]);

hold on;
yl4_hi = 1.25 * max([nanmax(kpi_damp_ax(mask_st)); nanmax(kpi_damp_ay(mask_st)); ...
                     damp_ref_ax95; damp_ref_ay95; eps]);
yl4_hi = max(yl4_hi, 0.001);

for ii = 1:numel(i_s)
    x1 = t_win(i_s(ii));
    x2 = t_win(min(i_e(ii)+1, nWin));
    fill([x1 x2 x2 x1], [0 0 yl4_hi yl4_hi], ...
        C_STOP, 'EdgeColor', 'none', 'FaceAlpha', 0.40, 'HandleVisibility', 'off');
end

h1 = scatter(t_win(mask_st), kpi_damp_ax(mask_st), 7, C_AX, 'filled', ...
    'MarkerFaceAlpha', 0.40, 'DisplayName', 'Damp AX (mm)');
h2 = scatter(t_win(mask_st), kpi_damp_ay(mask_st), 7, C_AY, 'filled', ...
    'MarkerFaceAlpha', 0.40, 'DisplayName', 'Damp AY (mm)');
h3 = plot(t_win, ewma_damp_ax, '-', 'Color', C_AX, 'LineWidth', 1.8, 'DisplayName', 'EWMA AX');
h4 = plot(t_win, ewma_damp_ay, '-', 'Color', C_AY, 'LineWidth', 1.8, 'DisplayName', 'EWMA AY');

yline(damp_ref_ax95, '--', 'Color', C_WARN, 'LineWidth', 1.2, ...
    'Label', sprintf('P95 AX %.2f mm', damp_ref_ax95), ...
    'LabelHorizontalAlignment', 'left', 'HandleVisibility', 'off');
yline(damp_ref_ay95, ':', 'Color', C_WARN*0.85, 'LineWidth', 1.2, ...
    'Label', sprintf('P95 AY %.2f mm', damp_ref_ay95), ...
    'LabelHorizontalAlignment', 'left', 'HandleVisibility', 'off');

grid on;
ylim([0 yl4_hi]);
set(gca, 'XLim', xl, 'FontSize', 14);
xlabel('Temps UTC', 'FontSize', 14);
ylabel('Damp (mm)', 'FontSize', 14);
xtickformat('dd/MM/yy'); xtickangle(10);
title(sprintf('Damp temporel resonance [%.2f-%.2f Hz] | double integration | P95 ref uniquement', ...
    f_res_lo, f_res_hi), 'FontSize', 14);
legend([h1 h2 h3 h4], 'Location', 'best', 'FontSize', 13, 'NumColumns', 2);

sgtitle(sprintf('[%s] Deplacement amplitude tete de tour | H=%.0f m', ...
    upper(turbine_id), H_tour), ...
    'FontSize', 15, 'FontWeight', 'bold');
%% SAUVEGARDE
outpath = uigetdir(fp_a, 'Dossier sauvegarde');
if isequal(outpath, 0), outpath = fp_a; end

figs_to_save = [1 2 3 4];
for fignum = figs_to_save
    fh = figure(fignum);
    if isgraphics(fh)
        fname = sprintf('Fig%02d_%s.png', fignum, turbine_id);
        exportgraphics(fh, fullfile(outpath, fname), 'Resolution', 180);
        fprintf('  %s\n', fname);
    end
end

fid = fopen(fullfile(outpath, sprintf('rapport_%s.txt', turbine_id)), 'w');
fprintf(fid, '=== RAPPORT V5.5 SIMPLIFIE - %s ===\n', upper(turbine_id));
fprintf(fid, 'Plage : %s - %s\n', char(t_utc(1)), char(t_utc(end)));
fprintf(fid, 'Duree : %.1f h | %d fen. de %d min\n', t_total_h, nWin, win_min);
fprintf(fid, 'fs = %d Hz\n', fs);
fprintf(fid, 'En marche : %d/%d | Stables : %d\n', sum(mask_on), nWin, n_st);
fprintf(fid, 'f0 ref AX = %.4f Hz | AY = %.4f Hz\n', f0_ref_ax, f0_ref_ay);
fprintf(fid, 'Derive AX = %.5f Hz/j | AY = %.5f Hz/j\n', drift_ax, drift_ay);
fprintf(fid, 'Bandes RMS : BF[%.2f-%.2f] Hz | Res[%.2f-%.2f] Hz\n', ...
    f_bf_lo, f_bf_hi, f_res_lo, f_res_hi);
fprintf(fid, 'OMA FDD : f0 = %.4f Hz | Amort. = %.2f %%\n', f0_fdd, damp_fdd);
fprintf(fid, 'Modal Env : AX f0 = %.4f Hz zeta = %.2f %% | AY f0 = %.4f Hz zeta = %.2f %%\n', ...
    f0_md_ax, zeta_md_ax, f0_md_ay, zeta_md_ay);
fprintf(fid, 'FDD vs ref : AX match = %s | AY match = %s\n', ...
    yesno(fdd_match_ax), yesno(fdd_match_ay));
fprintf(fid, 'Depl. RMS res max spectral : AX = %.4f mm | AY = %.4f mm\n', ...
    max(kpi_disp_res_ax, [], 'omitnan'), max(kpi_disp_res_ay, [], 'omitnan'));
fprintf(fid, 'Depl. amp temporel max : AX = %.4f mm | AY = %.4f mm\n', ...
    max(kpi_damp_ax, [], 'omitnan'), max(kpi_damp_ay, [], 'omitnan'));
fclose(fid);

fprintf('\n=== FIN - %s ===\n', upper(turbine_id));
%% LOCAL FUNCTIONS
function [f0e,ze,f1e,f2e]=env_modal_params(fc,above,f_s_lo,f_s_hi,hp_bw)
    f0e=NaN; ze=NaN; f1e=NaN; f2e=NaN;
    fc=fc(:); above=above(:);
    if numel(fc)~=numel(above), return; end
    band=fc>=f_s_lo&fc<=f_s_hi&isfinite(above);
    if sum(band)<3, return; end
    [pk,ip]=max(above(band)); fb=fc(band); f0e=fb(ip);
    hp_lev=pk*hp_bw^2; idx_band=find(band); ip_g=idx_band(ip);
    left=1:ip_g-1; right=ip_g+1:numel(fc);
    if ~isempty(left), il=find(above(left)<=hp_lev,1,'last'); if ~isempty(il), f1e=fc(left(il)); end; end
    if ~isempty(right), ir=find(above(right)<=hp_lev,1,'first'); if ~isempty(ir), f2e=fc(right(ir)); end; end
    if isfinite(f1e)&&isfinite(f2e)&&f0e>0, ze=(f2e-f1e)/(2*f0e)*100; end
end

function s=yesno(v)
    if v, s='OUI'; else, s='NON'; end
end
