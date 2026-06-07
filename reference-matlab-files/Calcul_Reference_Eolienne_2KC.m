%% ============================================================
% CALCUL REFERENCE EOLIENNE - Script A
% Version 2.0
% But : Construire une référence robuste à long terme (durée de vie éolienne)
%       à partir de données "probablement saines" sans certification formelle.
%
% Nouveautés V2 :
%   - Troncature robuste par bin (P5-P90) avant calcul des seuils P95/P99.5
%   - Bandes séparées : BF [0.05-0.25Hz] et Résonance [0.25-0.35Hz]
%   - Diagnostic qualité par bin : Bon / Limite / Insuffisant
%   - Rapport de qualité complet dans le CSV
%   - Figure de validation (histogrammes + seuils par bin)
%
% Workflow :
%   1. Exécuter CE script sur données probablement saines (2-6 mois)
%      -> produit REF_<turbine>_<label>.mat + CSV qualite
%   2. Exécuter le script de surveillance V5.4
%      -> charge le .mat : reference figee pour toute la duree de vie
%% ============================================================
clear; close all; clc;
set(0,'DefaultAxesFontSize',16);
set(0,'DefaultAxesFontName','Arial');
set(0,'DefaultTextFontSize',16);
set(0,'DefaultTextFontName','Arial');
set(0,'DefaultLegendFontSize',14);
set(0,'DefaultAxesTitleFontSizeMultiplier',1.1);

%% 1. SELECTION TURBINE
turbines_disp = {'W003','W005','W007'};
[idx_t,ok_t]=listdlg('PromptString','Selectionner la turbine :',...
    'SelectionMode','single','ListString',turbines_disp,'ListSize',[220 100],'Name','Turbine');
if ~ok_t, error('Annule.'); end
turbine_id = lower(turbines_disp{idx_t});
fprintf('=== Calcul référence : %s ===\n', upper(turbine_id));

%% 2. PARAMETRES (identiques au script de surveillance)
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
f_lo=0.20; f_hi=0.40; f_s_lo=0.31; f_s_hi=0.34; f_bb_lo=0.05; f_bb_hi=0.45; harm_bw=0.020;
% --- Bandes RMS (cohérent avec surveillance V5.4) ---
f_bf_lo=0.05;  f_bf_hi=0.25;   % Basse fréquence (turbulence, rafales)
f_res_lo=0.25; f_res_hi=0.35;  % Résonance (mode propre de la tour)
% --- Troncature robuste (V2) ---
% Exclure les P_trunc_lo% bas et P_trunc_hi% hauts avant calcul des seuils
% → élimine les transitoires, arrêts non détectés, dégradations minoritaires
P_trunc_lo = 5;   % % bas à exclure (capteur off, arrêts résiduels)
P_trunc_hi = 10;  % % haut à exclure (dégradations potentielles, rafales extrêmes)
N_bin_bon   = 100; % nb de pts stables minimum pour qualité "Bon"
N_bin_limit = 30;  % nb de pts stables minimum pour qualité "Limite"
% --------------------------------------------------------
win_min=10; ovlp=0.50; N_trans=3; tol_f0=0.05; seuil_kW=10; v_min_ctx=5.0; p_min_ctx=0.05;
fmt_list={'yyyy-MM-dd HH:mm:ss','yyyy-MM-dd HH:mm:ss.SSSSSS','dd/MM/yyyy HH:mm:ss','MM/dd/yyyy HH:mm:ss'};
vbins=[3 5 7 9 11 Inf]; nb=numel(vbins)-1;

%% 3. CHARGEMENT ACCELERO
fprintf('=== Fichiers accéléro de référence ===\n');
[fn_a,fp_a]=uigetfile('*.csv','Accéléro REF (Ctrl+clic=multi)','MultiSelect','on');
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

% Detection fs robuste
dt_med=seconds(median(diff(t_utc(1:min(500,N)))));
if dt_med<=0 || ~isfinite(dt_med)
    t_span=seconds(t_utc(end)-t_utc(1));
    if t_span>0 && N>1
        dt_med=t_span/(N-1);
        fprintf('  Timestamps peu résolus -> fs estimé depuis durée totale\n');
    else
        dt_med=1;
    end
end
fs=max(1,round(1/dt_med));
fprintf('  fs=%d Hz | %d pts | %.1f h\n',fs,N,N/fs/3600);

%% 4. CHARGEMENT SCADA
fprintf('=== Fichiers SCADA de référence ===\n');
[fn_s,fp_s]=uigetfile('*.csv','SCADA REF (Annuler=sans)','MultiSelect','on');
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
            vars = T_sc.Properties.VariableNames; found='';
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
                fprintf('  Colonne temps SCADA : "%s" (auto-détecté)\n',found);
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
win_s=round(win_min*60*fs); step_s=round(win_s*(1-ovlp));
i0=1:step_s:(N-win_s+1); nWin=numel(i0);
nfft=min(2^nextpow2(win_s), 2^17);
wham=hamming(win_s); wfac=wham'*wham;
fp=(0:nfft/2)/nfft*fs;
m_nb=fp>=f_lo&fp<=f_hi; m_s=fp>=f_s_lo&fp<=f_s_hi;
m_bb=fp>=f_bb_lo&fp<=f_bb_hi;
m_bf =fp>=f_bf_lo &fp<=f_bf_hi;
m_res=fp>=f_res_lo&fp<=f_res_hi;
df=mean(diff(fp));

t_win=NaT(nWin,1,'TimeZone',t_utc.TimeZone); t_win.Format=t_utc.Format;
kpi_f0ax=nan(nWin,1); kpi_f0ay=nan(nWin,1);
kpi_rax=nan(nWin,1);  kpi_ray=nan(nWin,1);
kpi_bb_ax=nan(nWin,1);
kpi_bf_ax=nan(nWin,1); kpi_bf_ay=nan(nWin,1);
kpi_res_ax=nan(nWin,1); kpi_res_ay=nan(nWin,1);
kpi_pow=nan(nWin,1);  kpi_wind=nan(nWin,1);
kpi_rpm=nan(nWin,1);  rpm_v_win=false(nWin,1);

fprintf('  PSD glissante (%d fenêtres)...\n',nWin);
t0_psd=tic;
for k=1:nWin
    idx=i0(k):(i0(k)+win_s-1);
    t_win(k)=t_utc(idx(round(end/2)));
    kpi_pow(k)=mean(power_ts(idx),'omitnan');
    kpi_wind(k)=mean(wind_ts(idx),'omitnan');
    kpi_rpm(k)=mean(rpm_est(idx),'omitnan');
    rpm_v_win(k)=mean(rpm_valid(idx),'omitnan')>0.5;
    rpm_k=kpi_rpm(k);
    for iax=1:2
        sw=ax_f(idx)*(iax==1)+ay_f(idx)*(iax==2);
        X=fft(detrend(sw).*wham,nfft);
        Pxx=(2/fs)*abs(X(1:nfft/2+1)).^2/wfac;
        rms_nb=sqrt(sum(Pxx(m_nb))*df);
        rms_bb=sqrt(sum(Pxx(m_bb))*df);
        rms_bf =sqrt(sum(Pxx(m_bf ))*df);
        rms_res=sqrt(sum(Pxx(m_res))*df);
        Ps=Pxx(m_s); fs_vec=fp(m_s);
        mh=true(size(fs_vec));
        if isfinite(rpm_k)&&rpm_k>1
            f1P=rpm_k/60;
            for h=1:4, mh=mh&(abs(fs_vec-h*f1P)>harm_bw); end
        end
        Ps_c=Ps; Ps_c(~mh)=0;
        f0_k=NaN;
        if any(Ps_c>0)
            [pk_val,im]=max(Ps_c);
            fond_med=median(Ps_c(Ps_c>0),'omitnan');
            if pk_val > fond_med*4
                f0_k=fs_vec(im);
            end
        end
        if iax==1
            kpi_f0ax(k)=f0_k; kpi_rax(k)=rms_nb; kpi_bb_ax(k)=rms_bb;
            kpi_bf_ax(k)=rms_bf; kpi_res_ax(k)=rms_res;
        else
            kpi_f0ay(k)=f0_k; kpi_ray(k)=rms_nb;
            kpi_bf_ay(k)=rms_bf; kpi_res_ay(k)=rms_res;
        end
        end
end
fprintf('  OK en %.0f s\n',toc(t0_psd));

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

%% 9. CALCUL f0 DE REFERENCE
amp_thr_ax=prctile(kpi_rax(mask_st),40);
amp_thr_ay=prctile(kpi_ray(mask_st),40);
f0ax_fbl=mask_st&isfinite(kpi_f0ax)&kpi_rax>=amp_thr_ax;
f0ay_fbl=mask_st&isfinite(kpi_f0ay)&kpi_ray>=amp_thr_ay;
mask_ref_f0=mask_st&f0ax_fbl&f0ay_fbl;
if has_wind&&any(isfinite(kpi_wind))
    mw=isfinite(kpi_wind)&kpi_wind>5&kpi_wind<9;
    if sum(mask_ref_f0&mw)>=10, mask_ref_f0=mask_ref_f0&mw; end
end
f0_ref_ax=median(kpi_f0ax(mask_ref_f0),'omitnan');
f0_ref_ay=median(kpi_f0ay(mask_ref_f0),'omitnan');
fprintf('  f0 ref : AX=%.4f Hz | AY=%.4f Hz\n',f0_ref_ax,f0_ref_ay);

%% 10. SEUILS ROBUSTES PAR BIN DE VENT (V2 — troncature P5-P90)
% Principe : pour chaque bin, on tronque les extrêmes avant de calculer
% les percentiles → les seuils sont insensibles aux dégradations minoritaires
% et aux transitoires présents dans les données "probablement saines".

if has_wind&&any(isfinite(kpi_wind))
    bin_id=zeros(nWin,1);
    for ib=1:nb
        m=isfinite(kpi_wind)&kpi_wind>=vbins(ib)&kpi_wind<vbins(ib+1);
        bin_id(m)=ib;
    end
else
    bin_id=ones(nWin,1); bin_id(~mask_st)=0;
    fprintf('  ATTENTION : pas de vent SCADA — un seul bin, référence moins précise\n');
end

% Initialisation
p95_ax=nan(nb,1);  p99_ax=nan(nb,1);
p95_ay=nan(nb,1);  p99_ay=nan(nb,1);
p95_bb=nan(nb,1);  p99_bb=nan(nb,1);
p95_bf_ax=nan(nb,1); p99_bf_ax=nan(nb,1);
p95_bf_ay=nan(nb,1); p99_bf_ay=nan(nb,1);
p95_res_ax=nan(nb,1); p99_res_ax=nan(nb,1);
p95_res_ay=nan(nb,1); p99_res_ay=nan(nb,1);
n_per_bin=zeros(nb,1);
n_trunc_bin=zeros(nb,1);
qualite_bin=repmat({'INSUFFISANT'},nb,1);

bin_labels_court={'[3-5]','[5-7]','[7-9]','[9-11]','[>11]'};

fprintf('\n  === DIAGNOSTIC QUALITE PAR BIN DE VENT ===\n');
fprintf('  %-12s %6s %6s %8s %10s %10s  Qualite\n',...
    'Bin','N_tot','N_tronc','BF_P95_AX','Res_P95_AX','Res_P99_AX');

for ib=1:nb
    mb_all = mask_st & bin_id==ib;
    n_per_bin(ib) = sum(mb_all);

    if n_per_bin(ib) < N_bin_limit
        % Insuffisant : pas assez de points
        qualite_bin{ib} = 'INSUFFISANT';
        if isinf(vbins(ib+1))
            fprintf('  Bin%d [%.0f-inf] : %d pts -> INSUFFISANT (min=%d)\n',...
                ib,vbins(ib),n_per_bin(ib),N_bin_limit);
        else
            fprintf('  Bin%d [%.0f-%.0f] : %d pts -> INSUFFISANT (min=%d)\n',...
                ib,vbins(ib),vbins(ib+1),n_per_bin(ib),N_bin_limit);
        end
        continue;
    end

    % --- Troncature robuste par bin ---
    % On tronque sur le RMS résonance AX (indicateur principal)
    % même troncature appliquée à toutes les bandes pour cohérence temporelle
    vals_ref = kpi_res_ax(mb_all);
    lo_thr = prctile(vals_ref, P_trunc_lo);
    hi_thr = prctile(vals_ref, 100 - P_trunc_hi);
    mb_trunc = mb_all & kpi_res_ax >= lo_thr & kpi_res_ax <= hi_thr;
    n_trunc_bin(ib) = sum(mb_trunc);

    if n_trunc_bin(ib) < max(10, N_bin_limit*0.5)
        qualite_bin{ib} = 'INSUFFISANT';
        fprintf('  Bin%d : après troncature %d pts -> INSUFFISANT\n',ib,n_trunc_bin(ib));
        continue;
    end

    % --- Calcul des percentiles sur données tronquées ---
    p95_ax(ib)  = prctile(kpi_rax(mb_trunc),95);
    p99_ax(ib)  = prctile(kpi_rax(mb_trunc),99.5);
    p95_ay(ib)  = prctile(kpi_ray(mb_trunc),95);
    p99_ay(ib)  = prctile(kpi_ray(mb_trunc),99.5);
    p95_bb(ib)  = prctile(kpi_bb_ax(mb_trunc),95);
    p99_bb(ib)  = prctile(kpi_bb_ax(mb_trunc),99.5);
    p95_bf_ax(ib)  = prctile(kpi_bf_ax(mb_trunc),95);
    p99_bf_ax(ib)  = prctile(kpi_bf_ax(mb_trunc),99.5);
    p95_bf_ay(ib)  = prctile(kpi_bf_ay(mb_trunc),95);
    p99_bf_ay(ib)  = prctile(kpi_bf_ay(mb_trunc),99.5);
    p95_res_ax(ib) = prctile(kpi_res_ax(mb_trunc),95);
    p99_res_ax(ib) = prctile(kpi_res_ax(mb_trunc),99.5);
    p95_res_ay(ib) = prctile(kpi_res_ay(mb_trunc),95);
    p99_res_ay(ib) = prctile(kpi_res_ay(mb_trunc),99.5);

    % Qualité
    if n_per_bin(ib) >= N_bin_bon
        qualite_bin{ib} = 'BON';
    else
        qualite_bin{ib} = 'LIMITE';
    end

    if isinf(vbins(ib+1))
        bl=sprintf('[%.0f-inf]',vbins(ib));
    else
        bl=sprintf('[%.0f-%.0f]',vbins(ib),vbins(ib+1));
    end
    fprintf('  Bin%d %-9s %6d %6d %10.2e %10.2e %10.2e  %s\n',...
        ib,bl,n_per_bin(ib),n_trunc_bin(ib),...
        p95_bf_ax(ib),p95_res_ax(ib),p99_res_ax(ib),qualite_bin{ib});
end

% --- Valeurs de repli (médiane des bins valides) ---
bins_valides = ~strcmp(qualite_bin,'INSUFFISANT');
rep_ax95  = nanmedian(p95_ax(bins_valides));
rep_ax99  = nanmedian(p99_ax(bins_valides));
rep_ay95  = nanmedian(p95_ay(bins_valides));
rep_ay99  = nanmedian(p99_ay(bins_valides));
rep_bb95  = nanmedian(p95_bb(bins_valides));
rep_bb99  = nanmedian(p99_bb(bins_valides));
rep_bf_ax95  = nanmedian(p95_bf_ax(bins_valides));
rep_bf_ax99  = nanmedian(p99_bf_ax(bins_valides));
rep_bf_ay95  = nanmedian(p95_bf_ay(bins_valides));
rep_bf_ay99  = nanmedian(p99_bf_ay(bins_valides));
rep_res_ax95 = nanmedian(p95_res_ax(bins_valides));
rep_res_ax99 = nanmedian(p99_res_ax(bins_valides));
rep_res_ay95 = nanmedian(p95_res_ay(bins_valides));
rep_res_ay99 = nanmedian(p99_res_ay(bins_valides));

n_bon     = sum(strcmp(qualite_bin,'BON'));
n_limite  = sum(strcmp(qualite_bin,'LIMITE'));
n_insuf   = sum(strcmp(qualite_bin,'INSUFFISANT'));
fprintf('\n  Résumé qualité : %d BON | %d LIMITE | %d INSUFFISANT\n',n_bon,n_limite,n_insuf);
if n_insuf > 0
    fprintf('  ATTENTION : %d bin(s) insuffisant(s) → seuil de repli utilisé\n',n_insuf);
    fprintf('  → Augmenter la durée de référence ou vérifier la couverture de vent\n');
end
fprintf('  Seuils repli P99.5 : Rés AX=%.2e | Rés AY=%.2e\n',rep_res_ax99,rep_res_ay99);

% --- Seuils de déplacement dérivés des seuils RMS acc (§10b_disp) ---
f_centre_res = (f_res_lo + f_res_hi) / 2;
f_centre_bf  = (f_bf_lo  + f_bf_hi ) / 2;
omg_res = (2*pi*f_centre_res)^2;
omg_bf  = (2*pi*f_centre_bf )^2;
% Par bin
p95_disp_res_ax = p95_res_ax / omg_res * 1000;  p99_disp_res_ax = p99_res_ax / omg_res * 1000;
p95_disp_res_ay = p95_res_ay / omg_res * 1000;  p99_disp_res_ay = p99_res_ay / omg_res * 1000;
p95_disp_bf_ax  = p95_bf_ax  / omg_bf  * 1000;  p99_disp_bf_ax  = p99_bf_ax  / omg_bf  * 1000;
p95_disp_bf_ay  = p95_bf_ay  / omg_bf  * 1000;  p99_disp_bf_ay  = p99_bf_ay  / omg_bf  * 1000;
% Repli global
rep_disp_res_ax95 = rep_res_ax95/omg_res*1000;  rep_disp_res_ax99 = rep_res_ax99/omg_res*1000;
rep_disp_res_ay95 = rep_res_ay95/omg_res*1000;  rep_disp_res_ay99 = rep_res_ay99/omg_res*1000;
rep_disp_bf_ax95  = rep_bf_ax95 /omg_bf *1000;  rep_disp_bf_ax99  = rep_bf_ax99 /omg_bf *1000;
rep_disp_bf_ay95  = rep_bf_ay95 /omg_bf *1000;  rep_disp_bf_ay99  = rep_bf_ay99 /omg_bf *1000;
fprintf('  Seuils deplacement resonance (repli) : P95 AX=%.3fmm AY=%.3fmm | P99.5 AX=%.3fmm AY=%.3fmm\n',...
    rep_disp_res_ax95,rep_disp_res_ay95,rep_disp_res_ax99,rep_disp_res_ay99);

%% 10b. FIGURE DE VALIDATION — histogrammes RMS résonance par bin
figure('Name','Validation référence — RMS Résonance par bin','NumberTitle','off',...
    'Position',[100 80 1400 700]);
n_cols = min(nb, 5);
for ib=1:nb
    subplot(2, n_cols, ib); hold on;
    mb_all = mask_st & bin_id==ib;
    if sum(mb_all) < 3, title(sprintf('Bin%d — vide',ib)); continue; end
    vals = kpi_res_ax(mb_all);
    histogram(vals, 30, 'FaceColor',[0.4 0.6 0.9],'EdgeColor','none','FaceAlpha',0.7,...
        'DisplayName','RMS Rés AX');
    if isfinite(p95_res_ax(ib))
        xline(p95_res_ax(ib),'--','Color',[1 0.6 0],'LineWidth',1.5,...
            'Label','P95','LabelVerticalAlignment','bottom');
        xline(p99_res_ax(ib),'-','Color',[0.85 0.1 0.1],'LineWidth',1.5,...
            'Label','P99.5','LabelVerticalAlignment','bottom');
    end
    lo_thr=prctile(vals,P_trunc_lo); hi_thr=prctile(vals,100-P_trunc_hi);
    xline(lo_thr,':k','LineWidth',1,'Label',sprintf('P%d',P_trunc_lo),...
        'LabelVerticalAlignment','top');
    xline(hi_thr,':k','LineWidth',1,'Label',sprintf('P%d',100-P_trunc_hi),...
        'LabelVerticalAlignment','top');
    if isinf(vbins(ib+1))
        tit=sprintf('Bin%d [>%.0fm/s]\nn=%d — %s',ib,vbins(ib),n_per_bin(ib),qualite_bin{ib});
    else
        tit=sprintf('Bin%d [%.0f-%.0fm/s]\nn=%d — %s',ib,vbins(ib),vbins(ib+1),n_per_bin(ib),qualite_bin{ib});
    end
    title(tit,'FontSize',13);
    xlabel('RMS Rés AX (m/s²)','FontSize',12); grid on;

    subplot(2, n_cols, ib+n_cols); hold on;
    vals_ay = kpi_res_ay(mb_all);
    histogram(vals_ay, 30,'FaceColor',[0.3 0.7 0.4],'EdgeColor','none','FaceAlpha',0.7,...
        'DisplayName','RMS Rés AY');
    if isfinite(p95_res_ay(ib))
        xline(p95_res_ay(ib),'--','Color',[1 0.6 0],'LineWidth',1.5,'Label','P95',...
            'LabelVerticalAlignment','bottom');
        xline(p99_res_ay(ib),'-','Color',[0.85 0.1 0.1],'LineWidth',1.5,'Label','P99.5',...
            'LabelVerticalAlignment','bottom');
    end
    xlabel('RMS Rés AY (m/s²)','FontSize',12);
    title(sprintf('AY — Bin%d',ib),'FontSize',13); grid on;
end
sgtitle(sprintf('[%s] Validation référence V2 — troncature P%d-P%d | %d fenêtres stables',...
    upper(turbine_id),P_trunc_lo,100-P_trunc_hi,n_st),'FontSize',16,'FontWeight','bold');
ref_label = inputdlg({'Description courte de cette référence (ex: Hiver2025-calme):'}, ...
    'Label référence', [1 60], {sprintf('%s_ref_%s',upper(turbine_id), datestr(now,'yyyymmdd'))});
if isempty(ref_label), ref_label={'ref_sans_label'}; end
ref_label = ref_label{1};

%% 12. SAUVEGARDE .MAT
outpath=uigetdir(fp_a,'Dossier de sauvegarde de la référence');
if isequal(outpath,0), outpath=fp_a; end

mat_fname=fullfile(outpath, sprintf('REF_%s_%s.mat', upper(turbine_id), ...
    strrep(ref_label,[upper(turbine_id) '_ref_'],'') ));

% Métadonnées de la référence
ref_meta.turbine_id   = turbine_id;
ref_meta.label        = ref_label;
ref_meta.version      = '2.1';
ref_meta.created_on   = datestr(now,'yyyy-mm-dd HH:MM:SS');
ref_meta.t_start      = char(t_utc(1));
ref_meta.t_end        = char(t_utc(end));
ref_meta.n_win_total  = nWin;
ref_meta.n_win_stable = n_st;
ref_meta.fs           = fs;
ref_meta.win_min      = win_min;
ref_meta.has_wind     = has_wind;
ref_meta.has_pow      = has_pow;
ref_meta.vbins        = vbins;
ref_meta.n_per_bin    = n_per_bin;
ref_meta.n_trunc_bin  = n_trunc_bin;
ref_meta.qualite_bin  = qualite_bin;
ref_meta.P_trunc_lo   = P_trunc_lo;
ref_meta.P_trunc_hi   = P_trunc_hi;
ref_meta.f_bf_lo      = f_bf_lo;
ref_meta.f_bf_hi      = f_bf_hi;
ref_meta.f_res_lo     = f_res_lo;
ref_meta.f_res_hi     = f_res_hi;
ref_meta.f_centre_res = f_centre_res;
ref_meta.f_centre_bf  = f_centre_bf;
ref_meta.H_tour       = H_tour;

save(mat_fname, ...
    'ref_meta', ...
    'f0_ref_ax','f0_ref_ay', ...
    'p95_ax','p99_ax','p95_ay','p99_ay','p95_bb','p99_bb', ...
    'p95_bf_ax','p99_bf_ax','p95_bf_ay','p99_bf_ay', ...
    'p95_res_ax','p99_res_ax','p95_res_ay','p99_res_ay', ...
    'p95_disp_res_ax','p99_disp_res_ax','p95_disp_res_ay','p99_disp_res_ay', ...
    'p95_disp_bf_ax', 'p99_disp_bf_ax', 'p95_disp_bf_ay', 'p99_disp_bf_ay', ...
    'rep_ax95','rep_ax99','rep_ay95','rep_ay99','rep_bb95','rep_bb99', ...
    'rep_bf_ax95','rep_bf_ax99','rep_bf_ay95','rep_bf_ay99', ...
    'rep_res_ax95','rep_res_ax99','rep_res_ay95','rep_res_ay99', ...
    'rep_disp_res_ax95','rep_disp_res_ax99','rep_disp_res_ay95','rep_disp_res_ay99', ...
    'rep_disp_bf_ax95', 'rep_disp_bf_ax99', 'rep_disp_bf_ay95', 'rep_disp_bf_ay99', ...
    'vbins','nb');
fprintf('  Sauvegardé : %s\n', mat_fname);

%% 13. SAUVEGARDE .CSV DE SYNTHESE
csv_fname=fullfile(outpath, sprintf('REF_%s_%s_synthese.csv', upper(turbine_id), ...
    strrep(ref_label,[upper(turbine_id) '_ref_'],'') ));

fid=fopen(csv_fname,'w','n','UTF-8');
fprintf(fid,'=== REFERENCE EOLIENNE V2 - RAPPORT QUALITE ===\n');
fprintf(fid,'Turbine;%s\n',upper(turbine_id));
fprintf(fid,'Label;%s\n',ref_label);
fprintf(fid,'Version;2.1\n');
fprintf(fid,'Cree le;%s\n',ref_meta.created_on);
fprintf(fid,'Hauteur tour (m);%.0f\n',H_tour);
fprintf(fid,'Periode;%s -> %s\n',ref_meta.t_start,ref_meta.t_end);
fprintf(fid,'Fenetres stables;%d / %d\n',n_st,nWin);
fprintf(fid,'fs (Hz);%d\n',fs);
fprintf(fid,'Vent SCADA;%d\n',has_wind);
fprintf(fid,'Troncature;P%d bas / P%d haut exclus\n',P_trunc_lo,P_trunc_hi);
fprintf(fid,'Bande BF;[%.2f-%.2f Hz]\n',f_bf_lo,f_bf_hi);
fprintf(fid,'Bande Resonance;[%.2f-%.2f Hz]\n',f_res_lo,f_res_hi);
fprintf(fid,'\n');
fprintf(fid,'--- Qualite par bin ---\n');
fprintf(fid,'Bin;Plage (m/s);N_total;N_apres_troncature;Qualite\n');
for ib=1:nb
    if isinf(vbins(ib+1))
        plage=sprintf('>%.0f',vbins(ib));
    else
        plage=sprintf('%.0f-%.0f',vbins(ib),vbins(ib+1));
    end
    fprintf(fid,'Bin%d;%s;%d;%d;%s\n',ib,plage,n_per_bin(ib),n_trunc_bin(ib),qualite_bin{ib});
end
fprintf(fid,'Qualite globale;%d BON / %d LIMITE / %d INSUFFISANT\n',n_bon,n_limite,n_insuf);
fprintf(fid,'\n');
fprintf(fid,'--- Frequences propres de reference ---\n');
fprintf(fid,'f0_ref_AX (Hz);%.5f\n',f0_ref_ax);
fprintf(fid,'f0_ref_AY (Hz);%.5f\n',f0_ref_ay);
fprintf(fid,'\n');
fprintf(fid,'--- Seuils de repli (mediane des bins valides) ---\n');
fprintf(fid,'Grandeur;P95;P99.5\n');
fprintf(fid,'BF_AX;%.6e;%.6e\n',rep_bf_ax95,rep_bf_ax99);
fprintf(fid,'BF_AY;%.6e;%.6e\n',rep_bf_ay95,rep_bf_ay99);
fprintf(fid,'Res_AX;%.6e;%.6e\n',rep_res_ax95,rep_res_ax99);
fprintf(fid,'Res_AY;%.6e;%.6e\n',rep_res_ay95,rep_res_ay99);
fprintf(fid,'\n');
fprintf(fid,'--- Seuils de repli deplacement (mm) ---\n');
fprintf(fid,'Grandeur;P95;P99.5\n');
fprintf(fid,'Disp_Res_AX (mm);%.4f;%.4f\n',rep_disp_res_ax95,rep_disp_res_ax99);
fprintf(fid,'Disp_Res_AY (mm);%.4f;%.4f\n',rep_disp_res_ay95,rep_disp_res_ay99);
fprintf(fid,'Disp_BF_AX  (mm);%.4f;%.4f\n',rep_disp_bf_ax95, rep_disp_bf_ax99);
fprintf(fid,'Disp_BF_AY  (mm);%.4f;%.4f\n',rep_disp_bf_ay95, rep_disp_bf_ay99);
fprintf(fid,'\n');
fprintf(fid,'--- Seuils robustes par bin de vent ---\n');
fprintf(fid,'Bin;Plage;N_trunc;P95_BF_AX;P99.5_BF_AX;P95_BF_AY;P99.5_BF_AY;P95_Res_AX;P99.5_Res_AX;P95_Res_AY;P99.5_Res_AY;Qualite\n');
for ib=1:nb
    if isinf(vbins(ib+1)), plage=sprintf('>%.0f',vbins(ib));
    else, plage=sprintf('%.0f-%.0f',vbins(ib),vbins(ib+1)); end
    fprintf(fid,'Bin%d;%s;%d;%.6e;%.6e;%.6e;%.6e;%.6e;%.6e;%.6e;%.6e;%s\n',...
        ib,plage,n_trunc_bin(ib),...
        p95_bf_ax(ib),p99_bf_ax(ib),p95_bf_ay(ib),p99_bf_ay(ib),...
        p95_res_ax(ib),p99_res_ax(ib),p95_res_ay(ib),p99_res_ay(ib),...
        qualite_bin{ib});
end
fclose(fid);
fprintf('  Synthèse CSV : %s\n', csv_fname);

fprintf('\n=== REFERENCE CALCULEE ET SAUVEGARDEE ===\n');
fprintf('  Fichier .mat : %s\n', mat_fname);
fprintf('  Fichier .csv : %s\n', csv_fname);
fprintf('  Chargez le .mat dans le script de surveillance (V5.3) pour l''utiliser.\n');
%% ============================================================
% CALCUL REFERENCE EOLIENNE - Script A
% Version 2.0
% But : Construire une référence robuste à long terme (durée de vie éolienne)
%       à partir de données "probablement saines" sans certification formelle.
%
% Nouveautés V2 :
%   - Troncature robuste par bin (P5-P90) avant calcul des seuils P95/P99.5
%   - Bandes séparées : BF [0.05-0.25Hz] et Résonance [0.25-0.35Hz]
%   - Diagnostic qualité par bin : Bon / Limite / Insuffisant
%   - Rapport de qualité complet dans le CSV
%   - Figure de validation (histogrammes + seuils par bin)
%
% Workflow :
%   1. Exécuter CE script sur données probablement saines (2-6 mois)
%      -> produit REF_<turbine>_<label>.mat + CSV qualite
%   2. Exécuter le script de surveillance V5.4
%      -> charge le .mat : reference figee pour toute la duree de vie
%% ============================================================
clear; close all; clc;
set(0,'DefaultAxesFontSize',16);
set(0,'DefaultAxesFontName','Arial');
set(0,'DefaultTextFontSize',16);
set(0,'DefaultTextFontName','Arial');
set(0,'DefaultLegendFontSize',14);
set(0,'DefaultAxesTitleFontSizeMultiplier',1.1);

%% 1. SELECTION TURBINE
turbines_disp = {'W003','W005','W007'};
[idx_t,ok_t]=listdlg('PromptString','Selectionner la turbine :',...
    'SelectionMode','single','ListString',turbines_disp,'ListSize',[220 100],'Name','Turbine');
if ~ok_t, error('Annule.'); end
turbine_id = lower(turbines_disp{idx_t});
fprintf('=== Calcul référence : %s ===\n', upper(turbine_id));

%% 2. PARAMETRES (identiques au script de surveillance)
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
f_lo=0.20; f_hi=0.40; f_s_lo=0.31; f_s_hi=0.34; f_bb_lo=0.05; f_bb_hi=0.45; harm_bw=0.020;
% --- Bandes RMS (cohérent avec surveillance V5.4) ---
f_bf_lo=0.05;  f_bf_hi=0.25;   % Basse fréquence (turbulence, rafales)
f_res_lo=0.25; f_res_hi=0.35;  % Résonance (mode propre de la tour)
% --- Troncature robuste (V2) ---
% Exclure les P_trunc_lo% bas et P_trunc_hi% hauts avant calcul des seuils
% → élimine les transitoires, arrêts non détectés, dégradations minoritaires
P_trunc_lo = 5;   % % bas à exclure (capteur off, arrêts résiduels)
P_trunc_hi = 10;  % % haut à exclure (dégradations potentielles, rafales extrêmes)
N_bin_bon   = 100; % nb de pts stables minimum pour qualité "Bon"
N_bin_limit = 30;  % nb de pts stables minimum pour qualité "Limite"
% --------------------------------------------------------
win_min=10; ovlp=0.50; N_trans=3; tol_f0=0.05; seuil_kW=10; v_min_ctx=5.0; p_min_ctx=0.05;
fmt_list={'yyyy-MM-dd HH:mm:ss','yyyy-MM-dd HH:mm:ss.SSSSSS','dd/MM/yyyy HH:mm:ss','MM/dd/yyyy HH:mm:ss'};
vbins=[3 5 7 9 11 Inf]; nb=numel(vbins)-1;

%% 3. CHARGEMENT ACCELERO
fprintf('=== Fichiers accéléro de référence ===\n');
[fn_a,fp_a]=uigetfile('*.csv','Accéléro REF (Ctrl+clic=multi)','MultiSelect','on');
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

% Detection fs robuste
dt_med=seconds(median(diff(t_utc(1:min(500,N)))));
if dt_med<=0 || ~isfinite(dt_med)
    t_span=seconds(t_utc(end)-t_utc(1));
    if t_span>0 && N>1
        dt_med=t_span/(N-1);
        fprintf('  Timestamps peu résolus -> fs estimé depuis durée totale\n');
    else
        dt_med=1;
    end
end
fs=max(1,round(1/dt_med));
fprintf('  fs=%d Hz | %d pts | %.1f h\n',fs,N,N/fs/3600);

%% 4. CHARGEMENT SCADA
fprintf('=== Fichiers SCADA de référence ===\n');
[fn_s,fp_s]=uigetfile('*.csv','SCADA REF (Annuler=sans)','MultiSelect','on');
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
            vars = T_sc.Properties.VariableNames; found='';
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
                fprintf('  Colonne temps SCADA : "%s" (auto-détecté)\n',found);
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
win_s=round(win_min*60*fs); step_s=round(win_s*(1-ovlp));
i0=1:step_s:(N-win_s+1); nWin=numel(i0);
nfft=min(2^nextpow2(win_s), 2^17);
wham=hamming(win_s); wfac=wham'*wham;
fp=(0:nfft/2)/nfft*fs;
m_nb=fp>=f_lo&fp<=f_hi; m_s=fp>=f_s_lo&fp<=f_s_hi;
m_bb=fp>=f_bb_lo&fp<=f_bb_hi;
m_bf =fp>=f_bf_lo &fp<=f_bf_hi;
m_res=fp>=f_res_lo&fp<=f_res_hi;
df=mean(diff(fp));

t_win=NaT(nWin,1,'TimeZone',t_utc.TimeZone); t_win.Format=t_utc.Format;
kpi_f0ax=nan(nWin,1); kpi_f0ay=nan(nWin,1);
kpi_rax=nan(nWin,1);  kpi_ray=nan(nWin,1);
kpi_bb_ax=nan(nWin,1);
kpi_bf_ax=nan(nWin,1); kpi_bf_ay=nan(nWin,1);
kpi_res_ax=nan(nWin,1); kpi_res_ay=nan(nWin,1);
kpi_pow=nan(nWin,1);  kpi_wind=nan(nWin,1);
kpi_rpm=nan(nWin,1);  rpm_v_win=false(nWin,1);

fprintf('  PSD glissante (%d fenêtres)...\n',nWin);
t0_psd=tic;
for k=1:nWin
    idx=i0(k):(i0(k)+win_s-1);
    t_win(k)=t_utc(idx(round(end/2)));
    kpi_pow(k)=mean(power_ts(idx),'omitnan');
    kpi_wind(k)=mean(wind_ts(idx),'omitnan');
    kpi_rpm(k)=mean(rpm_est(idx),'omitnan');
    rpm_v_win(k)=mean(rpm_valid(idx),'omitnan')>0.5;
    rpm_k=kpi_rpm(k);
    for iax=1:2
        sw=ax_f(idx)*(iax==1)+ay_f(idx)*(iax==2);
        X=fft(detrend(sw).*wham,nfft);
        Pxx=(2/fs)*abs(X(1:nfft/2+1)).^2/wfac;
        rms_nb=sqrt(sum(Pxx(m_nb))*df);
        rms_bb=sqrt(sum(Pxx(m_bb))*df);
        rms_bf =sqrt(sum(Pxx(m_bf ))*df);
        rms_res=sqrt(sum(Pxx(m_res))*df);
        Ps=Pxx(m_s); fs_vec=fp(m_s);
        mh=true(size(fs_vec));
        if isfinite(rpm_k)&&rpm_k>1
            f1P=rpm_k/60;
            for h=1:4, mh=mh&(abs(fs_vec-h*f1P)>harm_bw); end
        end
        Ps_c=Ps; Ps_c(~mh)=0;
        f0_k=NaN;
        if any(Ps_c>0)
            [pk_val,im]=max(Ps_c);
            fond_med=median(Ps_c(Ps_c>0),'omitnan');
            if pk_val > fond_med*4
                f0_k=fs_vec(im);
            end
        end
        if iax==1
            kpi_f0ax(k)=f0_k; kpi_rax(k)=rms_nb; kpi_bb_ax(k)=rms_bb;
            kpi_bf_ax(k)=rms_bf; kpi_res_ax(k)=rms_res;
        else
            kpi_f0ay(k)=f0_k; kpi_ray(k)=rms_nb;
            kpi_bf_ay(k)=rms_bf; kpi_res_ay(k)=rms_res;
        end
        end
end
fprintf('  OK en %.0f s\n',toc(t0_psd));

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

%% 9. CALCUL f0 DE REFERENCE
amp_thr_ax=prctile(kpi_rax(mask_st),40);
amp_thr_ay=prctile(kpi_ray(mask_st),40);
f0ax_fbl=mask_st&isfinite(kpi_f0ax)&kpi_rax>=amp_thr_ax;
f0ay_fbl=mask_st&isfinite(kpi_f0ay)&kpi_ray>=amp_thr_ay;
mask_ref_f0=mask_st&f0ax_fbl&f0ay_fbl;
if has_wind&&any(isfinite(kpi_wind))
    mw=isfinite(kpi_wind)&kpi_wind>5&kpi_wind<9;
    if sum(mask_ref_f0&mw)>=10, mask_ref_f0=mask_ref_f0&mw; end
end
f0_ref_ax=median(kpi_f0ax(mask_ref_f0),'omitnan');
f0_ref_ay=median(kpi_f0ay(mask_ref_f0),'omitnan');
fprintf('  f0 ref : AX=%.4f Hz | AY=%.4f Hz\n',f0_ref_ax,f0_ref_ay);

%% 10. SEUILS ROBUSTES PAR BIN DE VENT (V2 — troncature P5-P90)
% Principe : pour chaque bin, on tronque les extrêmes avant de calculer
% les percentiles → les seuils sont insensibles aux dégradations minoritaires
% et aux transitoires présents dans les données "probablement saines".

if has_wind&&any(isfinite(kpi_wind))
    bin_id=zeros(nWin,1);
    for ib=1:nb
        m=isfinite(kpi_wind)&kpi_wind>=vbins(ib)&kpi_wind<vbins(ib+1);
        bin_id(m)=ib;
    end
else
    bin_id=ones(nWin,1); bin_id(~mask_st)=0;
    fprintf('  ATTENTION : pas de vent SCADA — un seul bin, référence moins précise\n');
end

% Initialisation
p95_ax=nan(nb,1);  p99_ax=nan(nb,1);
p95_ay=nan(nb,1);  p99_ay=nan(nb,1);
p95_bb=nan(nb,1);  p99_bb=nan(nb,1);
p95_bf_ax=nan(nb,1); p99_bf_ax=nan(nb,1);
p95_bf_ay=nan(nb,1); p99_bf_ay=nan(nb,1);
p95_res_ax=nan(nb,1); p99_res_ax=nan(nb,1);
p95_res_ay=nan(nb,1); p99_res_ay=nan(nb,1);
n_per_bin=zeros(nb,1);
n_trunc_bin=zeros(nb,1);
qualite_bin=repmat({'INSUFFISANT'},nb,1);

bin_labels_court={'[3-5]','[5-7]','[7-9]','[9-11]','[>11]'};

fprintf('\n  === DIAGNOSTIC QUALITE PAR BIN DE VENT ===\n');
fprintf('  %-12s %6s %6s %8s %10s %10s  Qualite\n',...
    'Bin','N_tot','N_tronc','BF_P95_AX','Res_P95_AX','Res_P99_AX');

for ib=1:nb
    mb_all = mask_st & bin_id==ib;
    n_per_bin(ib) = sum(mb_all);

    if n_per_bin(ib) < N_bin_limit
        % Insuffisant : pas assez de points
        qualite_bin{ib} = 'INSUFFISANT';
        if isinf(vbins(ib+1))
            fprintf('  Bin%d [%.0f-inf] : %d pts -> INSUFFISANT (min=%d)\n',...
                ib,vbins(ib),n_per_bin(ib),N_bin_limit);
        else
            fprintf('  Bin%d [%.0f-%.0f] : %d pts -> INSUFFISANT (min=%d)\n',...
                ib,vbins(ib),vbins(ib+1),n_per_bin(ib),N_bin_limit);
        end
        continue;
    end

    % --- Troncature robuste par bin ---
    % On tronque sur le RMS résonance AX (indicateur principal)
    % même troncature appliquée à toutes les bandes pour cohérence temporelle
    vals_ref = kpi_res_ax(mb_all);
    lo_thr = prctile(vals_ref, P_trunc_lo);
    hi_thr = prctile(vals_ref, 100 - P_trunc_hi);
    mb_trunc = mb_all & kpi_res_ax >= lo_thr & kpi_res_ax <= hi_thr;
    n_trunc_bin(ib) = sum(mb_trunc);

    if n_trunc_bin(ib) < max(10, N_bin_limit*0.5)
        qualite_bin{ib} = 'INSUFFISANT';
        fprintf('  Bin%d : après troncature %d pts -> INSUFFISANT\n',ib,n_trunc_bin(ib));
        continue;
    end

    % --- Calcul des percentiles sur données tronquées ---
    p95_ax(ib)  = prctile(kpi_rax(mb_trunc),95);
    p99_ax(ib)  = prctile(kpi_rax(mb_trunc),99.5);
    p95_ay(ib)  = prctile(kpi_ray(mb_trunc),95);
    p99_ay(ib)  = prctile(kpi_ray(mb_trunc),99.5);
    p95_bb(ib)  = prctile(kpi_bb_ax(mb_trunc),95);
    p99_bb(ib)  = prctile(kpi_bb_ax(mb_trunc),99.5);
    p95_bf_ax(ib)  = prctile(kpi_bf_ax(mb_trunc),95);
    p99_bf_ax(ib)  = prctile(kpi_bf_ax(mb_trunc),99.5);
    p95_bf_ay(ib)  = prctile(kpi_bf_ay(mb_trunc),95);
    p99_bf_ay(ib)  = prctile(kpi_bf_ay(mb_trunc),99.5);
    p95_res_ax(ib) = prctile(kpi_res_ax(mb_trunc),95);
    p99_res_ax(ib) = prctile(kpi_res_ax(mb_trunc),99.5);
    p95_res_ay(ib) = prctile(kpi_res_ay(mb_trunc),95);
    p99_res_ay(ib) = prctile(kpi_res_ay(mb_trunc),99.5);

    % Qualité
    if n_per_bin(ib) >= N_bin_bon
        qualite_bin{ib} = 'BON';
    else
        qualite_bin{ib} = 'LIMITE';
    end

    if isinf(vbins(ib+1))
        bl=sprintf('[%.0f-inf]',vbins(ib));
    else
        bl=sprintf('[%.0f-%.0f]',vbins(ib),vbins(ib+1));
    end
    fprintf('  Bin%d %-9s %6d %6d %10.2e %10.2e %10.2e  %s\n',...
        ib,bl,n_per_bin(ib),n_trunc_bin(ib),...
        p95_bf_ax(ib),p95_res_ax(ib),p99_res_ax(ib),qualite_bin{ib});
end

% --- Valeurs de repli (médiane des bins valides) ---
bins_valides = ~strcmp(qualite_bin,'INSUFFISANT');
rep_ax95  = nanmedian(p95_ax(bins_valides));
rep_ax99  = nanmedian(p99_ax(bins_valides));
rep_ay95  = nanmedian(p95_ay(bins_valides));
rep_ay99  = nanmedian(p99_ay(bins_valides));
rep_bb95  = nanmedian(p95_bb(bins_valides));
rep_bb99  = nanmedian(p99_bb(bins_valides));
rep_bf_ax95  = nanmedian(p95_bf_ax(bins_valides));
rep_bf_ax99  = nanmedian(p99_bf_ax(bins_valides));
rep_bf_ay95  = nanmedian(p95_bf_ay(bins_valides));
rep_bf_ay99  = nanmedian(p99_bf_ay(bins_valides));
rep_res_ax95 = nanmedian(p95_res_ax(bins_valides));
rep_res_ax99 = nanmedian(p99_res_ax(bins_valides));
rep_res_ay95 = nanmedian(p95_res_ay(bins_valides));
rep_res_ay99 = nanmedian(p99_res_ay(bins_valides));

n_bon     = sum(strcmp(qualite_bin,'BON'));
n_limite  = sum(strcmp(qualite_bin,'LIMITE'));
n_insuf   = sum(strcmp(qualite_bin,'INSUFFISANT'));
fprintf('\n  Résumé qualité : %d BON | %d LIMITE | %d INSUFFISANT\n',n_bon,n_limite,n_insuf);
if n_insuf > 0
    fprintf('  ATTENTION : %d bin(s) insuffisant(s) → seuil de repli utilisé\n',n_insuf);
    fprintf('  → Augmenter la durée de référence ou vérifier la couverture de vent\n');
end
fprintf('  Seuils repli P99.5 : Rés AX=%.2e | Rés AY=%.2e\n',rep_res_ax99,rep_res_ay99);

% --- Seuils de déplacement dérivés des seuils RMS acc (§10b_disp) ---
f_centre_res = (f_res_lo + f_res_hi) / 2;
f_centre_bf  = (f_bf_lo  + f_bf_hi ) / 2;
omg_res = (2*pi*f_centre_res)^2;
omg_bf  = (2*pi*f_centre_bf )^2;
% Par bin
p95_disp_res_ax = p95_res_ax / omg_res * 1000;  p99_disp_res_ax = p99_res_ax / omg_res * 1000;
p95_disp_res_ay = p95_res_ay / omg_res * 1000;  p99_disp_res_ay = p99_res_ay / omg_res * 1000;
p95_disp_bf_ax  = p95_bf_ax  / omg_bf  * 1000;  p99_disp_bf_ax  = p99_bf_ax  / omg_bf  * 1000;
p95_disp_bf_ay  = p95_bf_ay  / omg_bf  * 1000;  p99_disp_bf_ay  = p99_bf_ay  / omg_bf  * 1000;
% Repli global
rep_disp_res_ax95 = rep_res_ax95/omg_res*1000;  rep_disp_res_ax99 = rep_res_ax99/omg_res*1000;
rep_disp_res_ay95 = rep_res_ay95/omg_res*1000;  rep_disp_res_ay99 = rep_res_ay99/omg_res*1000;
rep_disp_bf_ax95  = rep_bf_ax95 /omg_bf *1000;  rep_disp_bf_ax99  = rep_bf_ax99 /omg_bf *1000;
rep_disp_bf_ay95  = rep_bf_ay95 /omg_bf *1000;  rep_disp_bf_ay99  = rep_bf_ay99 /omg_bf *1000;
fprintf('  Seuils deplacement resonance (repli) : P95 AX=%.3fmm AY=%.3fmm | P99.5 AX=%.3fmm AY=%.3fmm\n',...
    rep_disp_res_ax95,rep_disp_res_ay95,rep_disp_res_ax99,rep_disp_res_ay99);

%% 10b. FIGURE DE VALIDATION — histogrammes RMS résonance par bin
figure('Name','Validation référence — RMS Résonance par bin','NumberTitle','off',...
    'Position',[100 80 1400 700]);
n_cols = min(nb, 5);
for ib=1:nb
    subplot(2, n_cols, ib); hold on;
    mb_all = mask_st & bin_id==ib;
    if sum(mb_all) < 3, title(sprintf('Bin%d — vide',ib)); continue; end
    vals = kpi_res_ax(mb_all);
    histogram(vals, 30, 'FaceColor',[0.4 0.6 0.9],'EdgeColor','none','FaceAlpha',0.7,...
        'DisplayName','RMS Rés AX');
    if isfinite(p95_res_ax(ib))
        xline(p95_res_ax(ib),'--','Color',[1 0.6 0],'LineWidth',1.5,...
            'Label','P95','LabelVerticalAlignment','bottom');
        xline(p99_res_ax(ib),'-','Color',[0.85 0.1 0.1],'LineWidth',1.5,...
            'Label','P99.5','LabelVerticalAlignment','bottom');
    end
    lo_thr=prctile(vals,P_trunc_lo); hi_thr=prctile(vals,100-P_trunc_hi);
    xline(lo_thr,':k','LineWidth',1,'Label',sprintf('P%d',P_trunc_lo),...
        'LabelVerticalAlignment','top');
    xline(hi_thr,':k','LineWidth',1,'Label',sprintf('P%d',100-P_trunc_hi),...
        'LabelVerticalAlignment','top');
    if isinf(vbins(ib+1))
        tit=sprintf('Bin%d [>%.0fm/s]\nn=%d — %s',ib,vbins(ib),n_per_bin(ib),qualite_bin{ib});
    else
        tit=sprintf('Bin%d [%.0f-%.0fm/s]\nn=%d — %s',ib,vbins(ib),vbins(ib+1),n_per_bin(ib),qualite_bin{ib});
    end
    title(tit,'FontSize',13);
    xlabel('RMS Rés AX (m/s²)','FontSize',12); grid on;

    subplot(2, n_cols, ib+n_cols); hold on;
    vals_ay = kpi_res_ay(mb_all);
    histogram(vals_ay, 30,'FaceColor',[0.3 0.7 0.4],'EdgeColor','none','FaceAlpha',0.7,...
        'DisplayName','RMS Rés AY');
    if isfinite(p95_res_ay(ib))
        xline(p95_res_ay(ib),'--','Color',[1 0.6 0],'LineWidth',1.5,'Label','P95',...
            'LabelVerticalAlignment','bottom');
        xline(p99_res_ay(ib),'-','Color',[0.85 0.1 0.1],'LineWidth',1.5,'Label','P99.5',...
            'LabelVerticalAlignment','bottom');
    end
    xlabel('RMS Rés AY (m/s²)','FontSize',12);
    title(sprintf('AY — Bin%d',ib),'FontSize',13); grid on;
end
sgtitle(sprintf('[%s] Validation référence V2 — troncature P%d-P%d | %d fenêtres stables',...
    upper(turbine_id),P_trunc_lo,100-P_trunc_hi,n_st),'FontSize',16,'FontWeight','bold');
ref_label = inputdlg({'Description courte de cette référence (ex: Hiver2025-calme):'}, ...
    'Label référence', [1 60], {sprintf('%s_ref_%s',upper(turbine_id), datestr(now,'yyyymmdd'))});
if isempty(ref_label), ref_label={'ref_sans_label'}; end
ref_label = ref_label{1};

%% 12. SAUVEGARDE .MAT
outpath=uigetdir(fp_a,'Dossier de sauvegarde de la référence');
if isequal(outpath,0), outpath=fp_a; end

mat_fname=fullfile(outpath, sprintf('REF_%s_%s.mat', upper(turbine_id), ...
    strrep(ref_label,[upper(turbine_id) '_ref_'],'') ));

% Métadonnées de la référence
ref_meta.turbine_id   = turbine_id;
ref_meta.label        = ref_label;
ref_meta.version      = '2.1';
ref_meta.created_on   = datestr(now,'yyyy-mm-dd HH:MM:SS');
ref_meta.t_start      = char(t_utc(1));
ref_meta.t_end        = char(t_utc(end));
ref_meta.n_win_total  = nWin;
ref_meta.n_win_stable = n_st;
ref_meta.fs           = fs;
ref_meta.win_min      = win_min;
ref_meta.has_wind     = has_wind;
ref_meta.has_pow      = has_pow;
ref_meta.vbins        = vbins;
ref_meta.n_per_bin    = n_per_bin;
ref_meta.n_trunc_bin  = n_trunc_bin;
ref_meta.qualite_bin  = qualite_bin;
ref_meta.P_trunc_lo   = P_trunc_lo;
ref_meta.P_trunc_hi   = P_trunc_hi;
ref_meta.f_bf_lo      = f_bf_lo;
ref_meta.f_bf_hi      = f_bf_hi;
ref_meta.f_res_lo     = f_res_lo;
ref_meta.f_res_hi     = f_res_hi;
ref_meta.f_centre_res = f_centre_res;
ref_meta.f_centre_bf  = f_centre_bf;
ref_meta.H_tour       = H_tour;

save(mat_fname, ...
    'ref_meta', ...
    'f0_ref_ax','f0_ref_ay', ...
    'p95_ax','p99_ax','p95_ay','p99_ay','p95_bb','p99_bb', ...
    'p95_bf_ax','p99_bf_ax','p95_bf_ay','p99_bf_ay', ...
    'p95_res_ax','p99_res_ax','p95_res_ay','p99_res_ay', ...
    'p95_disp_res_ax','p99_disp_res_ax','p95_disp_res_ay','p99_disp_res_ay', ...
    'p95_disp_bf_ax', 'p99_disp_bf_ax', 'p95_disp_bf_ay', 'p99_disp_bf_ay', ...
    'rep_ax95','rep_ax99','rep_ay95','rep_ay99','rep_bb95','rep_bb99', ...
    'rep_bf_ax95','rep_bf_ax99','rep_bf_ay95','rep_bf_ay99', ...
    'rep_res_ax95','rep_res_ax99','rep_res_ay95','rep_res_ay99', ...
    'rep_disp_res_ax95','rep_disp_res_ax99','rep_disp_res_ay95','rep_disp_res_ay99', ...
    'rep_disp_bf_ax95', 'rep_disp_bf_ax99', 'rep_disp_bf_ay95', 'rep_disp_bf_ay99', ...
    'vbins','nb');
fprintf('  Sauvegardé : %s\n', mat_fname);

%% 13. SAUVEGARDE .CSV DE SYNTHESE
csv_fname=fullfile(outpath, sprintf('REF_%s_%s_synthese.csv', upper(turbine_id), ...
    strrep(ref_label,[upper(turbine_id) '_ref_'],'') ));

fid=fopen(csv_fname,'w','n','UTF-8');
fprintf(fid,'=== REFERENCE EOLIENNE V2 - RAPPORT QUALITE ===\n');
fprintf(fid,'Turbine;%s\n',upper(turbine_id));
fprintf(fid,'Label;%s\n',ref_label);
fprintf(fid,'Version;2.1\n');
fprintf(fid,'Cree le;%s\n',ref_meta.created_on);
fprintf(fid,'Hauteur tour (m);%.0f\n',H_tour);
fprintf(fid,'Periode;%s -> %s\n',ref_meta.t_start,ref_meta.t_end);
fprintf(fid,'Fenetres stables;%d / %d\n',n_st,nWin);
fprintf(fid,'fs (Hz);%d\n',fs);
fprintf(fid,'Vent SCADA;%d\n',has_wind);
fprintf(fid,'Troncature;P%d bas / P%d haut exclus\n',P_trunc_lo,P_trunc_hi);
fprintf(fid,'Bande BF;[%.2f-%.2f Hz]\n',f_bf_lo,f_bf_hi);
fprintf(fid,'Bande Resonance;[%.2f-%.2f Hz]\n',f_res_lo,f_res_hi);
fprintf(fid,'\n');
fprintf(fid,'--- Qualite par bin ---\n');
fprintf(fid,'Bin;Plage (m/s);N_total;N_apres_troncature;Qualite\n');
for ib=1:nb
    if isinf(vbins(ib+1))
        plage=sprintf('>%.0f',vbins(ib));
    else
        plage=sprintf('%.0f-%.0f',vbins(ib),vbins(ib+1));
    end
    fprintf(fid,'Bin%d;%s;%d;%d;%s\n',ib,plage,n_per_bin(ib),n_trunc_bin(ib),qualite_bin{ib});
end
fprintf(fid,'Qualite globale;%d BON / %d LIMITE / %d INSUFFISANT\n',n_bon,n_limite,n_insuf);
fprintf(fid,'\n');
fprintf(fid,'--- Frequences propres de reference ---\n');
fprintf(fid,'f0_ref_AX (Hz);%.5f\n',f0_ref_ax);
fprintf(fid,'f0_ref_AY (Hz);%.5f\n',f0_ref_ay);
fprintf(fid,'\n');
fprintf(fid,'--- Seuils de repli (mediane des bins valides) ---\n');
fprintf(fid,'Grandeur;P95;P99.5\n');
fprintf(fid,'BF_AX;%.6e;%.6e\n',rep_bf_ax95,rep_bf_ax99);
fprintf(fid,'BF_AY;%.6e;%.6e\n',rep_bf_ay95,rep_bf_ay99);
fprintf(fid,'Res_AX;%.6e;%.6e\n',rep_res_ax95,rep_res_ax99);
fprintf(fid,'Res_AY;%.6e;%.6e\n',rep_res_ay95,rep_res_ay99);
fprintf(fid,'\n');
fprintf(fid,'--- Seuils de repli deplacement (mm) ---\n');
fprintf(fid,'Grandeur;P95;P99.5\n');
fprintf(fid,'Disp_Res_AX (mm);%.4f;%.4f\n',rep_disp_res_ax95,rep_disp_res_ax99);
fprintf(fid,'Disp_Res_AY (mm);%.4f;%.4f\n',rep_disp_res_ay95,rep_disp_res_ay99);
fprintf(fid,'Disp_BF_AX  (mm);%.4f;%.4f\n',rep_disp_bf_ax95, rep_disp_bf_ax99);
fprintf(fid,'Disp_BF_AY  (mm);%.4f;%.4f\n',rep_disp_bf_ay95, rep_disp_bf_ay99);
fprintf(fid,'\n');
fprintf(fid,'--- Seuils robustes par bin de vent ---\n');
fprintf(fid,'Bin;Plage;N_trunc;P95_BF_AX;P99.5_BF_AX;P95_BF_AY;P99.5_BF_AY;P95_Res_AX;P99.5_Res_AX;P95_Res_AY;P99.5_Res_AY;Qualite\n');
for ib=1:nb
    if isinf(vbins(ib+1)), plage=sprintf('>%.0f',vbins(ib));
    else, plage=sprintf('%.0f-%.0f',vbins(ib),vbins(ib+1)); end
    fprintf(fid,'Bin%d;%s;%d;%.6e;%.6e;%.6e;%.6e;%.6e;%.6e;%.6e;%.6e;%s\n',...
        ib,plage,n_trunc_bin(ib),...
        p95_bf_ax(ib),p99_bf_ax(ib),p95_bf_ay(ib),p99_bf_ay(ib),...
        p95_res_ax(ib),p99_res_ax(ib),p95_res_ay(ib),p99_res_ay(ib),...
        qualite_bin{ib});
end
fclose(fid);
fprintf('  Synthèse CSV : %s\n', csv_fname);

fprintf('\n=== REFERENCE CALCULEE ET SAUVEGARDEE ===\n');
fprintf('  Fichier .mat : %s\n', mat_fname);
fprintf('  Fichier .csv : %s\n', csv_fname);
fprintf('  Chargez le .mat dans le script de surveillance (V5.3) pour l''utiliser.\n');
