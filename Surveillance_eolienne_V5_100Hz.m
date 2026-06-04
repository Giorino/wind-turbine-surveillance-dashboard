%% ============================================================
% SURVEILLANCE EOLIENNES - MAT HYBRIDE BOIS-ACIER
% Version 5.1 - Dashboard industriel + Modal Diagram + OMA FDD
% Corrections : fs 100Hz | filtre f0 prominence | P99.5 | enveloppe modale
%% ============================================================
clear; close all; clc;

%% 1. SELECTION TURBINE
turbines_disp = {'W003','W005','W007'};
[idx_t,ok_t]=listdlg('PromptString','Selectionner la turbine :',...
    'SelectionMode','single','ListString',turbines_disp,'ListSize',[220 100],'Name','Turbine');
if ~ok_t, error('Annule.'); end
turbine_id = lower(turbines_disp{idx_t});
fprintf('=== Turbine : %s ===\n', upper(turbine_id));

%% 2. PARAMETRES
switch turbine_id
    case 'w003'; cutin_ms=2.50; v_z2_min=3.8; v_z2_max=8.5; P_nom_kW=500;  rpm_nom=13.0; f_rpm_res=7.0; rpm_marg=1.0;
    case 'w005'; cutin_ms=2.75; v_z2_min=4.0; v_z2_max=8.5; P_nom_kW=2000; rpm_nom=13.0; f_rpm_res=7.0; rpm_marg=1.0;
    case 'w007'; cutin_ms=2.25; v_z2_min=3.8; v_z2_max=8.5; P_nom_kW=2000; rpm_nom=13.0; f_rpm_res=7.0; rpm_marg=1.0;
    otherwise, error('Turbine inconnue.');
end
P_zone2_max=0.85*P_nom_kW;
col_time_acc='datetime'; col_ax='ax'; col_ay='ay';
col_time_sc='pointTime';
col_power=[turbine_id 'Power']; col_speed=[turbine_id 'Speed']; col_rpm_sc=[turbine_id 'RotorSpeed'];
tz_acc='Europe/Brussels'; tz_sc='UTC';
f_lo=0.20; f_hi=0.40; f_s_lo=0.31; f_s_hi=0.34; f_bb_lo=0.05; f_bb_hi=0.45; harm_bw=0.020;
win_min=10; ovlp=0.50; N_trans=3; tol_f0=0.05; seuil_kW=10; v_min_ctx=5.0; p_min_ctx=0.05;
ref_days=30; ewma_win=19; drift_alert=0.001;
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
T_acc=sortrows(T_acc,col_time_acc);
t_loc=T_acc.(col_time_acc);
t_utc=t_loc; t_utc.TimeZone='UTC'; t_utc.Format='yyyy-MM-dd HH:mm:ss Z';
N=height(T_acc);

% --- Detection fs robuste (fonctionne a 1Hz ET 100Hz) ---
% Si timestamps en precision seconde, diff=0 a 100Hz -> fallback duree totale
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
        % Auto-detection colonne temps si 'pointTime' absent
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
            else
                fprintf('  ERREUR : colonne temps SCADA introuvable. Colonnes : %s\n',strjoin(T_sc.Properties.VariableNames,', '));
            end
        end
        T_sc=sortrows(T_sc,col_time_sc);
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
% Securite : fn_lo et fn_hi doivent etre dans (0,1)
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
nfft=min(2^nextpow2(win_s), 2^17);   % limite 2^17 pour 100Hz
wham=hamming(win_s); wfac=wham'*wham;
fp=(0:nfft/2)/nfft*fs;
m_nb=fp>=f_lo&fp<=f_hi; m_s=fp>=f_s_lo&fp<=f_s_hi;
m_bb=fp>=f_bb_lo&fp<=f_bb_hi; df=mean(diff(fp));

t_win=NaT(nWin,1,'TimeZone',t_utc.TimeZone); t_win.Format=t_utc.Format;
kpi_f0ax=nan(nWin,1); kpi_f0ay=nan(nWin,1);
kpi_rax=nan(nWin,1);  kpi_ray=nan(nWin,1);
kpi_bb_ax=nan(nWin,1);kpi_bb_ay=nan(nWin,1);
kpi_1P_ax=nan(nWin,1);kpi_3P_ax=nan(nWin,1);
kpi_pow=nan(nWin,1);  kpi_wind=nan(nWin,1);
kpi_rpm=nan(nWin,1);  rpm_v_win=false(nWin,1);

fprintf('  PSD glissante (%d fen.)...\n',nWin);
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
        rms_1P=NaN; rms_3P=NaN;
        if isfinite(rpm_k)&&rpm_k>1
            f1P=rpm_k/60;
            m1P=fp>=(f1P-harm_bw)&fp<=(f1P+harm_bw);
            m3P=fp>=(3*f1P-harm_bw)&fp<=(3*f1P+harm_bw);
            if any(m1P), rms_1P=sqrt(sum(Pxx(m1P))*df); end
            if any(m3P), rms_3P=sqrt(sum(Pxx(m3P))*df); end
        end
        % Detection f0 avec filtre proeminence (+6dB au-dessus du fond)
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
            if pk_val > fond_med*4   % +6dB en puissance = rejet arttefacts
                f0_k=fs_vec(im);
            end
        end
        if iax==1
            kpi_f0ax(k)=f0_k; kpi_rax(k)=rms_nb;
            kpi_bb_ax(k)=rms_bb; kpi_1P_ax(k)=rms_1P; kpi_3P_ax(k)=rms_3P;
        else
            kpi_f0ay(k)=f0_k; kpi_ray(k)=rms_nb; kpi_bb_ay(k)=rms_bb;
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

%% 9. REFERENCES
t_ref_end=t_win(1)+days(ref_days);
mask_ref=mask_st&t_win<=t_ref_end;
if sum(mask_ref)<20, mask_ref=mask_st; end
amp_thr_ax=prctile(kpi_rax(mask_ref),40);
amp_thr_ay=prctile(kpi_ray(mask_ref),40);
f0ax_fbl=mask_st&isfinite(kpi_f0ax)&kpi_rax>=amp_thr_ax;
f0ay_fbl=mask_st&isfinite(kpi_f0ay)&kpi_ray>=amp_thr_ay;
mask_ref_f0=mask_ref&f0ax_fbl&f0ay_fbl;
if has_wind&&any(isfinite(kpi_wind))
    mw=isfinite(kpi_wind)&kpi_wind>5&kpi_wind<9;
    if sum(mask_ref_f0&mw)>=10, mask_ref_f0=mask_ref_f0&mw; end
end
f0_ref_ax=median(kpi_f0ax(mask_ref_f0),'omitnan');
f0_ref_ay=median(kpi_f0ay(mask_ref_f0),'omitnan');
f0_lo_ax=f0_ref_ax*(1-tol_f0); f0_hi_ax=f0_ref_ax*(1+tol_f0);
f0_lo_ay=f0_ref_ay*(1-tol_f0); f0_hi_ay=f0_ref_ay*(1+tol_f0);
fprintf('  f0 ref : AX=%.4f Hz | AY=%.4f Hz\n',f0_ref_ax,f0_ref_ay);

%% 10. SEUILS P95/P99.5 PAR BIN DE VENT
vbins=[3 5 7 9 11 Inf]; nb=numel(vbins)-1;
if has_wind&&any(isfinite(kpi_wind))
    bin_id=zeros(nWin,1);
    for ib=1:nb
        m=isfinite(kpi_wind)&kpi_wind>=vbins(ib)&kpi_wind<vbins(ib+1);
        bin_id(m)=ib;
    end
else
    bin_id=ones(nWin,1); bin_id(~mask_st)=0;
end
p95_ax=nan(nb,1); p99_ax=nan(nb,1); p95_ay=nan(nb,1); p99_ay=nan(nb,1);
p95_bb=nan(nb,1); p99_bb=nan(nb,1);
for ib=1:nb
    mb=mask_ref&bin_id==ib;
    if sum(mb)>=5
        p95_ax(ib)=prctile(kpi_rax(mb),95);   p99_ax(ib)=prctile(kpi_rax(mb),99.5);
        p95_ay(ib)=prctile(kpi_ray(mb),95);   p99_ay(ib)=prctile(kpi_ray(mb),99.5);
        p95_bb(ib)=prctile(kpi_bb_ax(mb),95); p99_bb(ib)=prctile(kpi_bb_ax(mb),99.5);
    end
end
s_p95_ax=nan(nWin,1); s_p99_ax=nan(nWin,1);
s_p95_ay=nan(nWin,1); s_p99_ay=nan(nWin,1);
s_p95_bb=nan(nWin,1); s_p99_bb=nan(nWin,1);
for k=1:nWin
    ib=bin_id(k);
    if ib>0
        if isfinite(p95_ax(ib)), s_p95_ax(k)=p95_ax(ib); s_p99_ax(k)=p99_ax(ib); end
        if isfinite(p95_ay(ib)), s_p95_ay(k)=p95_ay(ib); s_p99_ay(k)=p99_ay(ib); end
        if isfinite(p95_bb(ib)), s_p95_bb(k)=p95_bb(ib); s_p99_bb(k)=p99_bb(ib); end
    end
end
rep_ax95=nanmedian(p95_ax); rep_ax99=nanmedian(p99_ax);
rep_ay95=nanmedian(p95_ay); rep_ay99=nanmedian(p99_ay);
rep_bb95=nanmedian(p95_bb); rep_bb99=nanmedian(p99_bb);
s_p95_ax(isnan(s_p95_ax)&bin_id>0)=rep_ax95; s_p99_ax(isnan(s_p99_ax)&bin_id>0)=rep_ax99;
s_p95_ay(isnan(s_p95_ay)&bin_id>0)=rep_ay95; s_p99_ay(isnan(s_p99_ay)&bin_id>0)=rep_ay99;
s_p95_bb(isnan(s_p95_bb)&bin_id>0)=rep_bb95; s_p99_bb(isnan(s_p99_bb)&bin_id>0)=rep_bb99;
fprintf('  Seuils P99.5 : AX=%.2e | AY=%.2e\n',rep_ax99,rep_ay99);

%% 11. EWMA
tmp_ax=kpi_rax; tmp_ax(~mask_st)=NaN;
tmp_ay=kpi_ray; tmp_ay(~mask_st)=NaN;
tmp_bb=kpi_bb_ax; tmp_bb(~mask_st)=NaN;
ewma_ax=movmean(tmp_ax,ewma_win,'omitnan');
ewma_ay=movmean(tmp_ay,ewma_win,'omitnan');
ewma_bb=movmean(tmp_bb,ewma_win,'omitnan');
ewa_ab=isfinite(ewma_ax)&ewma_ax>s_p95_ax;
ewb_ab=isfinite(ewma_ay)&ewma_ay>s_p95_ay;
al_ewma_ax=ewa_ab&[false;ewa_ab(1:end-1)]&[false;false;ewa_ab(1:end-2)];
al_ewma_ay=ewb_ab&[false;ewb_ab(1:end-1)]&[false;false;ewb_ab(1:end-2)];

%% 12. ALERTES F0
cond_ctx=false(nWin,1);
if has_pow,  cond_ctx=cond_ctx|(isfinite(kpi_pow)&kpi_pow>p_min_ctx*P_nom_kW); end
if has_wind, cond_ctx=cond_ctx|(isfinite(kpi_wind)&kpi_wind>=v_min_ctx); end
if has_rpm_dir||any(rpm_v_win), cond_ctx=cond_ctx|(isfinite(kpi_rpm)&kpi_rpm>=5); end
if ~has_pow&&~has_wind, cond_ctx(:)=true; end
hb_ax=f0ax_fbl&mask_st&cond_ctx&(kpi_f0ax<f0_lo_ax|kpi_f0ax>f0_hi_ax);
hb_ay=f0ay_fbl&mask_st&cond_ctx&(kpi_f0ay<f0_lo_ay|kpi_f0ay>f0_hi_ay);
al_f0=(hb_ax&hb_ay)| ...
    (hb_ax&[false;hb_ax(1:end-1)]&[false;false;hb_ax(1:end-2)])| ...
    (hb_ay&[false;hb_ay(1:end-1)]&[false;false;hb_ay(1:end-2)]);

%% 13. ALERTES RMS + SCORE
al_rms_ax=mask_st&isfinite(kpi_rax)&kpi_rax>s_p99_ax;
al_rms_ay=mask_st&isfinite(kpi_ray)&kpi_ray>s_p99_ay;
al_rms=al_rms_ax|al_rms_ay|al_ewma_ax|al_ewma_ay;
info_ax=mask_st&isfinite(kpi_rax)&kpi_rax>s_p95_ax&~al_rms_ax;
info_ay=mask_st&isfinite(kpi_ray)&kpi_ray>s_p95_ay&~al_rms_ay;
score=zeros(nWin,1);
score=score+double(al_f0)+double(al_rms)+double(al_rms&al_f0);
al_confirmed=mask_st&score>=2;
al_warning=mask_st&score==1;
fprintf('  Alertes : f0=%d | RMS=%d | Score>=2=%d | Avert.=%d\n',...
    sum(al_f0),sum(al_rms),sum(al_confirmed),sum(al_warning));

%% 14. TENDANCE F0
t_days=days(t_win-t_win(1));
ok_ax=isfinite(kpi_f0ax)&f0ax_fbl&mask_st;
ok_ay=isfinite(kpi_f0ay)&f0ay_fbl&mask_st;
drift_ax=NaN; drift_ay=NaN; p_ax=[NaN NaN]; p_ay=[NaN NaN];
if sum(ok_ax)>20, p_ax=polyfit(t_days(ok_ax),kpi_f0ax(ok_ax),1); drift_ax=p_ax(1); end
if sum(ok_ay)>20, p_ay=polyfit(t_days(ok_ay),kpi_f0ay(ok_ay),1); drift_ay=p_ay(1); end
is_drft_ax=isfinite(drift_ax)&&abs(drift_ax)>drift_alert;
is_drft_ay=isfinite(drift_ay)&&abs(drift_ay)>drift_alert;
N_med=max(1,round(24*60/(win_min*(1-ovlp))));
v_ax=kpi_f0ax; v_ax(~f0ax_fbl)=NaN; trend_ax=movmedian(v_ax,N_med,'omitnan');
v_ay=kpi_f0ay; v_ay(~f0ay_fbl)=NaN; trend_ay=movmedian(v_ay,N_med,'omitnan');
fprintf('  Derive : AX=%.5f Hz/j | AY=%.5f Hz/j\n',drift_ax,drift_ay);

%% 15. COULEURS + ZONES ARRET
set(groot,'DefaultAxesFontSize',12,'DefaultLegendFontSize',10,'DefaultTextFontSize',10);
C_AX=[0.10 0.40 0.85]; C_AY=[0.05 0.55 0.20];
C_ALRT=[0.88 0.10 0.10]; C_WARN=[0.95 0.50 0.05];
C_OK=[0.15 0.65 0.15]; C_STOP=[0.88 0.88 0.88]; C_DRFT=[0.55 0.00 0.55];
mask_arr=~mask_on; dm=diff([0;mask_arr(:);0]);
i_s=find(dm==1); i_e=find(dm==-1)-1;
t_wv=t_win(~isnat(t_win));
xl=[t_wv(1)-minutes(30), t_wv(end)+minutes(30)];
t_total_h=nWin*win_min*(1-ovlp)/60;

%% FIG 1 - DASHBOARD SANTE
fig1=figure('Name',sprintf('[%s] Dashboard',upper(turbine_id)),'Color','w');
set(fig1,'Position',[20 20 1440 860]);

sp1=subplot(4,1,1); hold on;
yl_f0=[f_s_lo*0.93 f_s_hi*1.07];
for ii=1:numel(i_s)
    x1=t_win(i_s(ii)); x2=t_win(min(i_e(ii)+1,nWin));
    fill([x1 x2 x2 x1],[yl_f0(1) yl_f0(1) yl_f0(2) yl_f0(2)],C_STOP,'EdgeColor','none','FaceAlpha',0.40,'HandleVisibility','off');
end
scatter(t_win(f0ax_fbl),kpi_f0ax(f0ax_fbl),5,C_AX,'filled','MarkerFaceAlpha',0.45,'DisplayName','f0 AX');
scatter(t_win(f0ay_fbl),kpi_f0ay(f0ay_fbl),5,C_AY,'filled','MarkerFaceAlpha',0.45,'DisplayName','f0 AY');
plot(t_win,trend_ax,'-','Color',C_AX,'LineWidth',2,'DisplayName','Tend. AX');
plot(t_win,trend_ay,'-','Color',C_AY,'LineWidth',2,'DisplayName','Tend. AY');
patch([t_win(1) t_win(end) t_win(end) t_win(1)],[f0_lo_ax f0_lo_ax f0_hi_ax f0_hi_ax],C_AX,'FaceAlpha',0.06,'EdgeColor','none','HandleVisibility','off');
patch([t_win(1) t_win(end) t_win(end) t_win(1)],[f0_lo_ay f0_lo_ay f0_hi_ay f0_hi_ay],C_AY,'FaceAlpha',0.06,'EdgeColor','none','HandleVisibility','off');
yline(f0_ref_ax,'--','Color',C_AX,'LineWidth',1,'HandleVisibility','off');
yline(f0_ref_ay,'--','Color',C_AY,'LineWidth',1,'HandleVisibility','off');
if any(hb_ax), scatter(t_win(hb_ax),kpi_f0ax(hb_ax),40,C_ALRT,'filled','MarkerEdgeColor','k','DisplayName','Alerte AX'); end
if any(hb_ay), scatter(t_win(hb_ay),kpi_f0ay(hb_ay),40,C_ALRT,'filled','MarkerEdgeColor','k','DisplayName','Alerte AY'); end
cc_ax=C_DRFT*is_drft_ax+[0.65 0.65 0.65]*(~is_drft_ax);
cc_ay=C_DRFT*is_drft_ay+[0.65 0.65 0.65]*(~is_drft_ay);
if sum(ok_ax)>20
    plot(t_win(1)+days(t_days(ok_ax)),polyval(p_ax,t_days(ok_ax)),'--','Color',cc_ax,'LineWidth',1.2,'DisplayName',sprintf('Regr. AX %.5fHz/j',drift_ax));
end
if sum(ok_ay)>20
    plot(t_win(1)+days(t_days(ok_ay)),polyval(p_ay,t_days(ok_ay)),'--','Color',cc_ay,'LineWidth',1.2,'DisplayName',sprintf('Regr. AY %.5fHz/j',drift_ay));
end
drft_lbl=sprintf('AX:%.5fHz/j  AY:%.5fHz/j',drift_ax,drift_ay);
if is_drft_ax||is_drft_ay, drft_lbl=['! DERIVE: ' drft_lbl]; end
text(0.99,0.04,drft_lbl,'Units','normalized','FontSize',9,'BackgroundColor','w','EdgeColor',[0.7 0.7 0.7],'HorizontalAlignment','right');
grid on; ylim(yl_f0); ylabel('f0 (Hz)');
title(sprintf('[%s] f0 AX+AY | ref AX=%.4f AY=%.4f Hz | tol+-%.0f%%',upper(turbine_id),f0_ref_ax,f0_ref_ay,tol_f0*100));
legend('Location','best','FontSize',10,'NumColumns',3);
xtickformat('dd/MM/yy'); xtickangle(10); set(gca,'XLim',xl,'FontSize',11);

sp2=subplot(4,1,2); hold on;
yl_r=[0, 1.2*max([nanmax(kpi_bb_ax(mask_st)); nanmax(kpi_bb_ay(mask_st)); rep_bb99*1.1; eps])];
for ii=1:numel(i_s)
    x1=t_win(i_s(ii)); x2=t_win(min(i_e(ii)+1,nWin));
    fill([x1 x2 x2 x1],[yl_r(1) yl_r(1) yl_r(2) yl_r(2)],C_STOP,'EdgeColor','none','FaceAlpha',0.40,'HandleVisibility','off');
end
scatter(t_win(mask_st&~info_ax),kpi_bb_ax(mask_st&~info_ax),5,C_AX,'filled','MarkerFaceAlpha',0.35,'DisplayName','BB AX');
scatter(t_win(mask_st&~info_ay),kpi_bb_ay(mask_st&~info_ay),5,C_AY,'filled','MarkerFaceAlpha',0.35,'DisplayName','BB AY');
scatter(t_win(info_ax),kpi_bb_ax(info_ax),14,C_WARN,'filled','MarkerEdgeColor',C_AX,'LineWidth',0.7,'DisplayName','Info AX (P95)');
scatter(t_win(info_ay),kpi_bb_ay(info_ay),14,C_WARN,'filled','MarkerEdgeColor',C_AY,'LineWidth',0.7,'DisplayName','Info AY (P95)');
if any(al_rms_ax), scatter(t_win(al_rms_ax),kpi_bb_ax(al_rms_ax),25,C_ALRT,'filled','MarkerEdgeColor','k','DisplayName','Alerte AX'); end
if any(al_rms_ay), scatter(t_win(al_rms_ay),kpi_bb_ay(al_rms_ay),25,C_ALRT,'filled','MarkerEdgeColor','k','DisplayName','Alerte AY'); end
plot(t_win,ewma_ax,'-','Color',C_AX,'LineWidth',1.4,'DisplayName','EWMA AX');
plot(t_win,ewma_ay,'-','Color',C_AY,'LineWidth',1.4,'DisplayName','EWMA AY');
yline(rep_bb95,'--','Color',C_WARN,'LineWidth',0.9,'HandleVisibility','off');
yline(rep_bb99,'--','Color',C_ALRT,'LineWidth',1.0,'HandleVisibility','off');
grid on; ylim(yl_r); ylabel('RMS (m/s2)');
title(sprintf('RMS broadband [%.2f-%.2f Hz] | orange=P95 | rouge=P99.5 | ligne=EWMA',f_bb_lo,f_bb_hi));
legend('Location','best','FontSize',10,'NumColumns',3);
xtickformat('dd/MM/yy'); xtickangle(10); set(gca,'XLim',xl,'FontSize',11);

sp3=subplot(4,1,3); hold on;
for ii=1:numel(i_s)
    x1=t_win(i_s(ii)); x2=t_win(min(i_e(ii)+1,nWin));
    fill([x1 x2 x2 x1],[-0.3 -0.3 3.5 3.5],C_STOP,'EdgeColor','none','FaceAlpha',0.40,'HandleVisibility','off');
end
scatter(t_win(mask_st&score==0),score(mask_st&score==0),6,C_OK,'filled','MarkerFaceAlpha',0.35,'HandleVisibility','off');
scatter(t_win(mask_st&score==1),score(mask_st&score==1),22,C_WARN,'filled','MarkerEdgeColor','k','LineWidth',0.5,'DisplayName',sprintf('Avert. (n=%d)',sum(al_warning)));
scatter(t_win(mask_st&score>=2),score(mask_st&score>=2),38,C_ALRT,'filled','MarkerEdgeColor','k','LineWidth',0.8,'DisplayName',sprintf('Alerte (n=%d)',sum(al_confirmed)));
yline(1.5,'--k','LineWidth',1,'HandleVisibility','off');
grid on; ylim([-0.3 3.5]); ylabel('Score 0-3');
yticks(0:3); yticklabels({'0-OK','1-Avert','2-Alerte','3-Crit.'});
title('Score de confiance (+1:f0 | +1:RMS | +1:co-occurrence)');
legend('Location','best','FontSize',11);
xtickformat('dd/MM/yy'); xtickangle(10); set(gca,'XLim',xl,'FontSize',11);

sp4=subplot(4,1,4); hold on;
rows4={al_f0,al_rms,al_confirmed,~mask_on}; lbl4={'f0','RMS','Score>=2','Arret'};
for row=1:4
    av=double(rows4{row}); tr_c=[1;find(diff(av)~=0)+1;nWin+1];
    for s_i=1:numel(tr_c)-1
        k1=tr_c(s_i); k2=tr_c(s_i+1)-1; val=av(k1);
        if row==4; cc=val*C_STOP+(1-val)*C_OK; elseif val>0; cc=C_ALRT; else; cc=C_OK; end
        fill([t_win(k1) t_win(min(k2+1,nWin)) t_win(min(k2+1,nWin)) t_win(k1)],[row-.45 row-.45 row+.45 row+.45],cc,'EdgeColor','none');
    end
end
set(sp4,'YTick',1:4,'YTickLabel',lbl4,'YLim',[0.5 4.5],'FontSize',11);
title('Carte alertes (vert=OK | rouge=alerte | gris=arret)');
xlabel('Temps UTC'); grid on;
xtickformat('dd/MM/yy'); xtickangle(10); set(gca,'XLim',xl);
linkaxes([sp1 sp2 sp3 sp4],'x');
sgtitle(sprintf('[%s] Dashboard | %.0fh | %d/%d fen | Score2+:%d Avert:%d',upper(turbine_id),t_total_h,sum(mask_on),nWin,sum(al_confirmed),sum(al_warning)),'FontSize',9,'FontWeight','bold');

%% FIG 2 - MODAL DIAGRAM avec enveloppes
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
    all_db=[all_db; Ax(fm_md)]; %#ok<AGROW>
end
fprintf(' OK\n');
thr_md=prctile(all_db,65);

% Enveloppes P10/P90 par bin de frequence
nbin_env=200;
f_env_edges=linspace(min(fp_md_s),max(fp_md_s),nbin_env+1);
f_env_c=f_env_edges(1:end-1)+diff(f_env_edges)/2;
env_lo_ax=nan(nbin_env,1); env_hi_ax=nan(nbin_env,1);
env_lo_ay=nan(nbin_env,1); env_hi_ay=nan(nbin_env,1);
for ib=1:nbin_env
    mi=fp_md_s>=f_env_edges(ib)&fp_md_s<f_env_edges(ib+1);
    if any(mi)
        vax=Xax_all(:,mi); vay=Xay_all(:,mi);
        env_lo_ax(ib)=prctile(vax(:),10); env_hi_ax(ib)=prctile(vax(:),90);
        env_lo_ay(ib)=prctile(vay(:),10); env_hi_ay(ib)=prctile(vay(:),90);
    end
end
above_ax=env_hi_ax-env_lo_ax; above_ay=env_hi_ay-env_lo_ay;

% f0 et amortissement par enveloppe
[f0_md_ax,zeta_md_ax,f1_ax,f2_ax]=env_modal_params(f_env_c,above_ax,f_s_lo,f_s_hi,hp_bw_frac);
[f0_md_ay,zeta_md_ay,f1_ay,f2_ay]=env_modal_params(f_env_c,above_ay,f_s_lo,f_s_hi,hp_bw_frac);
fprintf('  Enveloppe : AX f0=%.4fHz zeta=%.2f%% | AY f0=%.4fHz zeta=%.2f%%\n',f0_md_ax,zeta_md_ax,f0_md_ay,zeta_md_ay);
rpm_med=median(kpi_rpm(rpm_v_win),'omitnan');

fig2=figure('Name',sprintf('[%s] Modal Diagram',upper(turbine_id)),'Color','w');
set(fig2,'Position',[40 40 1400 760]);
for iax=1:2
    sp_md=subplot(2,1,iax); hold on;
    if iax==1; Xall=Xax_all; env_lo=env_lo_ax; env_hi=env_hi_ax; f0_e=f0_md_ax; ze_e=zeta_md_ax; f1_e=f1_ax; f2_e=f2_ax; C_t=C_AX; lbl='AX (fore-aft)';
    else;       Xall=Xay_all; env_lo=env_lo_ay; env_hi=env_hi_ay; f0_e=f0_md_ay; ze_e=zeta_md_ay; f1_e=f1_ay; f2_e=f2_ay; C_t=C_AY; lbl='AY (lateral)'; end
    all_f=[]; all_a=[];
    for ki=1:nsub
        row=Xall(ki,:); show=row>thr_md;
        if any(show), all_f=[all_f,fp_md_s(show)]; all_a=[all_a,row(show)]; end %#ok<AGROW>
    end
    if numel(all_f)>=2
        scatter(all_f(:),all_a(:),4,all_a(:),'filled','MarkerFaceAlpha',0.5,'MarkerEdgeAlpha',0);
    end
    colormap(sp_md,jet);
    cb=colorbar; cb.Label.String='dB'; cb.FontSize=10;
    clim_lo=double(thr_md(1)); clim_hi=double(max(all_a(:)));
    if isfinite(clim_lo)&&isfinite(clim_hi)&&clim_hi>clim_lo, clim([clim_lo clim_hi]); end
    plot(f_env_c,env_lo,'-w','LineWidth',1.2,'DisplayName','P10 (plancher bruit)');
    plot(f_env_c,env_hi,'--w','LineWidth',0.9,'DisplayName','P90');
    yl_md=ylim;
    if isfinite(f0_e)
        xline(f0_e,'-','Color',C_t,'LineWidth',2,'HandleVisibility','off');
        if isfinite(f1_e), xline(f1_e,':','Color',C_t,'LineWidth',1,'HandleVisibility','off'); end
        if isfinite(f2_e), xline(f2_e,':','Color',C_t,'LineWidth',1,'HandleVisibility','off'); end
        text(f0_e+0.003,yl_md(1)+0.88*diff(yl_md),sprintf('f0=%.4fHz\nzeta=%.2f%%',f0_e,ze_e),...
            'FontSize',9,'Color',C_t,'FontWeight','bold','BackgroundColor','w','EdgeColor',C_t,'VerticalAlignment','top');
    end
    if isfinite(rpm_med)&&rpm_med>1
        for h=1:6
            fh=h*rpm_med/60;
            if fh>=0.10&&fh<=0.80
                xline(fh,':','Color',[1.0 0.6 0.2],'LineWidth',1,'HandleVisibility','off');
                text(fh+0.002,yl_md(1)+0.06*diff(yl_md),sprintf('%dP',h),'FontSize',9,'Color',[0.8 0.4 0]);
            end
        end
    end
    grid on; xlabel('Frequence (Hz)','FontSize',12); ylabel('Amplitude (dB)','FontSize',12);
    title(sprintf('[%s] Modal Diagram %s | %d fen. 1h | f0=%.4fHz zeta=%.2f%%',upper(turbine_id),lbl,nsub,f0_e,ze_e),'FontSize',12);
    legend('Location','northwest','FontSize',10);
end
sgtitle(sprintf('[%s] Modal Diagram | AX: f0=%.4fHz zeta=%.2f%% | AY: f0=%.4fHz zeta=%.2f%%',upper(turbine_id),f0_md_ax,zeta_md_ax,f0_md_ay,zeta_md_ay),'FontSize',11,'FontWeight','bold');

%% FIG 3 - OMA FDD
fprintf('\n=== OMA FDD ===\n');
win_fdd=min(round(fdd_seg_min*60*fs),N);
nfft_fdd=min(2^nextpow2(win_fdd),2^17); step_fdd=round(win_fdd*(1-fdd_ovlp));
i0_fdd=1:step_fdd:(N-win_fdd+1); nW_fdd=numel(i0_fdd);
fp_fdd=(0:nfft_fdd/2)/nfft_fdd*fs; wfdd=hamming(win_fdd); nF_fdd=nfft_fdd/2+1;
Sxx=zeros(nF_fdd,1); Syy=zeros(nF_fdd,1); Sxy=zeros(nF_fdd,1,'like',1+1i); n_used=0;
fprintf('  %d segments...',nW_fdd);
for k=1:nW_fdd
    idx=i0_fdd(k):(i0_fdd(k)+win_fdd-1);
    t_mid=t_utc(idx(round(end/2))); [~,km]=min(abs(seconds(t_win-t_mid)));
    if ~mask_on(km), continue; end
    segx=detrend(ax_f(idx)).*wfdd; segy=detrend(ay_f(idx)).*wfdd;
    Fx=fft(segx,nfft_fdd)/nfft_fdd; Fy=fft(segy,nfft_fdd)/nfft_fdd;
    Fx=Fx(1:nF_fdd); Fy=Fy(1:nF_fdd);
    Sxx=Sxx+real(Fx.*conj(Fx)); Syy=Syy+real(Fy.*conj(Fy)); Sxy=Sxy+Fx.*conj(Fy);
    n_used=n_used+1;
end
Sxx=Sxx/max(n_used,1); Syy=Syy/max(n_used,1); Sxy=Sxy/max(n_used,1);
fprintf(' %d segments en marche\n',n_used);
s1=zeros(nF_fdd,1); s2=zeros(nF_fdd,1);
for f_idx=1:nF_fdd
    G=[Sxx(f_idx) Sxy(f_idx); conj(Sxy(f_idx)) Syy(f_idx)]; sv=svd(G); s1(f_idx)=sv(1); s2(f_idx)=sv(2);
end
coh=abs(Sxy).^2./max(Sxx.*Syy,eps);
fm_fdd=fp_fdd>=fdd_f_lo&fp_fdd<=fdd_f_hi;
fp_d=fp_fdd(fm_fdd); s1_dB=10*log10(s1(fm_fdd)); s2_dB=10*log10(s2(fm_fdd)); coh_d=coh(fm_fdd);
s1_sm=smoothdata(s1_dB,'gaussian',5); s1_lin=10.^(s1_sm/10);
band_s_fdd=fp_d>=f_s_lo&fp_d<=f_s_hi;
if isfinite(rpm_med)&&rpm_med>1
    f1P_med=rpm_med/60;
    for h=1:6, band_s_fdd=band_s_fdd&(abs(fp_d-h*f1P_med)>harm_bw); end
end
f0_fdd=NaN; damp_fdd=NaN; f1_hp=NaN; f2_hp=NaN;
if any(band_s_fdd)
    [~,ip]=max(s1_sm(band_s_fdd)); f_band=fp_d(band_s_fdd); f0_fdd=f_band(ip);
    idx_all=find(band_s_fdd); ip_g=idx_all(ip);
    pk_val=s1_lin(ip_g); hp_lev=pk_val*hp_bw_frac^2;
    left=1:ip_g-1; right=ip_g+1:numel(fp_d);
    if ~isempty(left); il=find(s1_lin(left)<=hp_lev,1,'last'); if ~isempty(il), f1_hp=fp_d(left(il)); end; end
    if ~isempty(right); ir=find(s1_lin(right)<=hp_lev,1,'first'); if ~isempty(ir), f2_hp=fp_d(right(ir)); end; end
    if isfinite(f1_hp)&&isfinite(f2_hp)&&f0_fdd>0, damp_fdd=(f2_hp-f1_hp)/(2*f0_fdd)*100; end
end
fprintf('  FDD : f0=%.4f Hz | amortissement=%.2f%%\n',f0_fdd,damp_fdd);

fig3=figure('Name',sprintf('[%s] OMA FDD',upper(turbine_id)),'Color','w');
set(fig3,'Position',[60 60 1200 780]);
sp_f1=subplot(3,1,1); hold on;
plot(fp_d,s1_dB,'-','Color',C_AX,'LineWidth',1.6,'DisplayName','s1 (mode dominant)');
plot(fp_d,s2_dB,'-','Color',[0.65 0.65 0.65],'LineWidth',0.9,'DisplayName','s2');
yl_f1=ylim;
fill([f_s_lo f_s_hi f_s_hi f_s_lo],[yl_f1(1) yl_f1(1) yl_f1(2) yl_f1(2)],C_AX,'FaceAlpha',0.06,'EdgeColor','none','HandleVisibility','off');
if isfinite(f0_fdd)
    xline(f0_fdd,'--r','LineWidth',1.3,'HandleVisibility','off');
    text(0.02,0.95,sprintf('f0 = %.4f Hz\nAmort. = %.2f %%',f0_fdd,damp_fdd),'Units','normalized','FontSize',10,'Color','r','FontWeight','bold','BackgroundColor','w','EdgeColor','r','VerticalAlignment','top');
    if isfinite(f1_hp), xline(f1_hp,':r','LineWidth',0.9,'HandleVisibility','off'); end
    if isfinite(f2_hp), xline(f2_hp,':r','LineWidth',0.9,'HandleVisibility','off'); end
end
if isfinite(rpm_med)&&rpm_med>1
    for h=1:6; fh=h*rpm_med/60;
        if fh>=fdd_f_lo&&fh<=fdd_f_hi
            xline(fh,':','Color',[0.7 0.3 0],'LineWidth',0.8,'HandleVisibility','off');
            text(fh+0.002,yl_f1(1)+0.05*diff(yl_f1),sprintf('%dP',h),'FontSize',8,'Color',[0.7 0.3 0]);
        end
    end
end
grid on; xlabel('Frequence (Hz)'); ylabel('Amplitude (dB)');
title(sprintf('[%s] FDD - Valeurs singulieres | %d segments %.0f min',upper(turbine_id),n_used,fdd_seg_min));
legend('Location','best','FontSize',11); xlim([fdd_f_lo fdd_f_hi]);

sp_f2=subplot(3,1,2); hold on;
plot(fp_d,coh_d,'-','Color',[0.20 0.50 0.80],'LineWidth',1.3,'DisplayName','Coherence AX-AY');
yline(0.8,'--r','LineWidth',1,'DisplayName','Seuil 0.8');
yline(0.6,':','Color',[0.8 0.5 0],'LineWidth',0.9,'DisplayName','Seuil 0.6');
if isfinite(f0_fdd), xline(f0_fdd,'--r','LineWidth',1.0,'HandleVisibility','off'); end
fill([f_s_lo f_s_hi f_s_hi f_s_lo],[0 0 1 1],C_AX,'FaceAlpha',0.06,'EdgeColor','none','HandleVisibility','off');
grid on; ylim([0 1]); xlabel('Frequence (Hz)'); ylabel('Coherence');
title('Coherence AX-AY (note: faible entre capteurs orthogonaux - normal pour 1 accelero 2 axes)');
legend('Location','best','FontSize',11); xlim([fdd_f_lo fdd_f_hi]);

sp_f3=subplot(3,1,3); hold on;
plot(fp_d,10*log10(Sxx(fm_fdd)+eps),'-','Color',C_AX,'LineWidth',1.2,'DisplayName','PSD AX');
plot(fp_d,10*log10(Syy(fm_fdd)+eps),'-','Color',C_AY,'LineWidth',1.2,'DisplayName','PSD AY');
if isfinite(f0_fdd), xline(f0_fdd,'--r','LineWidth',1.0,'HandleVisibility','off'); end
yl_f3=ylim; fill([f_s_lo f_s_hi f_s_hi f_s_lo],[yl_f3(1) yl_f3(1) yl_f3(2) yl_f3(2)],C_AX,'FaceAlpha',0.06,'EdgeColor','none','HandleVisibility','off');
grid on; xlabel('Frequence (Hz)'); ylabel('PSD (dB)');
title('PSD AX et AY'); legend('Location','best','FontSize',11); xlim([fdd_f_lo fdd_f_hi]);
sgtitle(sprintf('[%s] OMA FDD | f0=%.4fHz | Amort=%.2f%% | %d segments',upper(turbine_id),f0_fdd,damp_fdd,n_used),'FontSize',10,'FontWeight','bold');

%% SAUVEGARDE
outpath=uigetdir(fp_a,'Dossier sauvegarde');
if isequal(outpath,0), outpath=fp_a; end
for fignum=1:3
    fh=figure(fignum); fname=sprintf('Fig_%02d_%s.png',fignum,turbine_id);
    exportgraphics(fh,fullfile(outpath,fname),'Resolution',180);
    fprintf('  %s\n',fname);
end
fid=fopen(fullfile(outpath,sprintf('rapport_%s.txt',turbine_id)),'w');
fprintf(fid,'=== RAPPORT V5.1 - %s ===\n',upper(turbine_id));
fprintf(fid,'Plage : %s -> %s\n',char(t_utc(1)),char(t_utc(end)));
fprintf(fid,'Duree : %.1f h | %d fen. %d min\n',t_total_h,nWin,win_min);
fprintf(fid,'fs : %d Hz\n',fs);
fprintf(fid,'En marche : %d/%d | Stables : %d\n',sum(mask_on),nWin,n_st);
fprintf(fid,'f0 ref : AX=%.4f Hz | AY=%.4f Hz\n',f0_ref_ax,f0_ref_ay);
fprintf(fid,'Derive : AX=%.5f Hz/j | AY=%.5f Hz/j\n',drift_ax,drift_ay);
if is_drft_ax, fprintf(fid,'  *** DERIVE AX ***\n'); end
if is_drft_ay, fprintf(fid,'  *** DERIVE AY ***\n'); end
fprintf(fid,'Alertes : f0=%d | RMS=%d | Score>=2=%d | Avert=%d\n',sum(al_f0),sum(al_rms),sum(al_confirmed),sum(al_warning));
fprintf(fid,'OMA FDD : f0=%.4f Hz | Amort=%.2f%%\n',f0_fdd,damp_fdd);
fprintf(fid,'Modal Env: AX f0=%.4fHz zeta=%.2f%% | AY f0=%.4fHz zeta=%.2f%%\n',f0_md_ax,zeta_md_ax,f0_md_ay,zeta_md_ay);
fclose(fid);
fprintf('\n=== FIN - %s ===\n',upper(turbine_id));

%% LOCAL FUNCTIONS
function [f0e,ze,f1e,f2e] = env_modal_params(fc, above, f_s_lo, f_s_hi, hp_bw)
    f0e=NaN; ze=NaN; f1e=NaN; f2e=NaN;
    fc=fc(:); above=above(:);   % forcer en colonnes
    if numel(fc)~=numel(above), return; end
    band=fc>=f_s_lo&fc<=f_s_hi&isfinite(above);
    if sum(band)<3, return; end
    [pk,ip]=max(above(band)); fb=fc(band); f0e=fb(ip);
    hp_lev=pk*hp_bw^2;
    idx_band=find(band); ip_g=idx_band(ip);
    left=1:ip_g-1; right=ip_g+1:numel(fc);
    if ~isempty(left); il=find(above(left)<=hp_lev,1,'last'); if ~isempty(il), f1e=fc(left(il)); end; end
    if ~isempty(right); ir=find(above(right)<=hp_lev,1,'first'); if ~isempty(ir), f2e=fc(right(ir)); end; end
    if isfinite(f1e)&&isfinite(f2e)&&f0e>0, ze=(f2e-f1e)/(2*f0e)*100; end
end

function s = yesno(v) %#ok<DEFNU>
    if v, s='OUI'; else, s='NON'; end
end
