import { useCallback, useEffect, useRef, useState } from 'react';
import QRCode from 'qrcode';
import { QrCode, RefreshCw, Smartphone, Trash2 } from 'lucide-react';
import { apiFetch, getAdminPairCode } from '@/lib/api';
import { apiOrigin, basePath } from '@/lib/basePath';
import { useT } from '@/revka/hooks/useT';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import Notice from '../components/ui/Notice';
import StateMessage from '../components/ui/StateMessage';
import { copyToClipboard } from '../lib/clipboard';

interface Device {
  id: string;
  name: string | null;
  device_type: string | null;
  paired_at: string;
  last_seen: string;
  ip_address: string | null;
}

interface DevicesResponse {
  devices: Device[];
}

interface InitiateResponse {
  pairing_code: string;
}

function buildQrPayload(code: string): string {
  const origin = apiOrigin || window.location.origin;
  const path = basePath || '';
  return JSON.stringify({
    v: 1,
    type: 'revka-pair',
    host: `${origin}${path}`.replace(/\/+$/, ''),
    code,
  });
}

export default function Pairing() {
  const { t, tpl } = useT();
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [initiating, setInitiating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; message: string } | null>(null);

  const qrCancelRef = useRef(0);

  const fetchDevices = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiFetch<DevicesResponse>('/api/devices');
      setDevices(res.devices ?? []);
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('pairing.err.load_devices') });
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    getAdminPairCode()
      .then((data) => { if (data.pairing_code) setPairingCode(data.pairing_code); })
      .catch(() => {});
  }, []);

  useEffect(() => { fetchDevices(); }, [fetchDevices]);

  useEffect(() => {
    if (!pairingCode) {
      setQrDataUrl(null);
      return;
    }
    const ticket = ++qrCancelRef.current;
    QRCode.toDataURL(buildQrPayload(pairingCode), {
      errorCorrectionLevel: 'M',
      margin: 1,
      width: 256,
      color: { dark: '#0a0a0a', light: '#ffffff' },
    })
      .then((url) => {
        if (qrCancelRef.current === ticket) setQrDataUrl(url);
      })
      .catch(() => {
        if (qrCancelRef.current === ticket) setQrDataUrl(null);
      });
  }, [pairingCode]);

  const handleInitiate = async () => {
    setInitiating(true);
    try {
      const res = await apiFetch<InitiateResponse>('/api/pairing/initiate', { method: 'POST' });
      setPairingCode(res.pairing_code);
      setNotice({ tone: 'success', message: t('pairing.toast.code_generated') });
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('pairing.err.gen_code') });
    } finally {
      setInitiating(false);
    }
  };

  const handleRevoke = async (id: string) => {
    try {
      await apiFetch<void>(`/api/devices/${id}`, { method: 'DELETE' });
      setDevices((prev) => prev.filter((d) => d.id !== id));
      setNotice({ tone: 'success', message: t('pairing.toast.device_revoked') });
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('pairing.err.revoke') });
    } finally {
      setConfirmDelete(null);
    }
  };

  return (
    <div className="flex h-[calc(100vh-6rem)] flex-col gap-3">
      {notice ? <Notice tone={notice.tone} message={notice.message} onDismiss={() => setNotice(null)} /> : null}

      <PageHeader
        kicker={t('pairing.kicker')}
        title={t('pairing.title')}
        description={t('pairing.description')}
        actions={(
          <>
            <button className="revka-button" onClick={fetchDevices} disabled={loading}>
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              {t('pairing.reload')}
            </button>
            <button className="revka-button" data-variant="primary" onClick={handleInitiate} disabled={initiating}>
              <Smartphone className="h-4 w-4" />
              {initiating ? t('pairing.generating') : t('pairing.pair_new_device')}
            </button>
          </>
        )}
      />

      <div className="grid gap-3" style={{ gridTemplateColumns: pairingCode ? 'minmax(0, 1fr) 22rem' : 'minmax(0, 1fr)' }}>
        {pairingCode ? (
          <Panel className="p-5" variant="secondary">
            <div className="flex items-center gap-2">
              <QrCode className="h-4 w-4" style={{ color: 'var(--revka-signal-network)' }} />
              <div className="revka-kicker">{t('pairing.active_code')}</div>
            </div>

            <div className="mt-5 grid grid-cols-[auto_minmax(0,1fr)] gap-5">
              <div
                className="flex h-[224px] w-[224px] items-center justify-center overflow-hidden rounded-[12px] border"
                style={{ borderColor: 'var(--revka-border-soft)', background: '#ffffff' }}
              >
                {qrDataUrl ? (
                  <img src={qrDataUrl} alt="Pairing QR code" className="h-full w-full object-contain" />
                ) : (
                  <div className="text-xs" style={{ color: '#666' }}>{t('pairing.rendering')}</div>
                )}
              </div>
              <div className="flex min-w-0 flex-col justify-between">
                <div>
                  <div
                    className="rounded-[12px] border px-4 py-3 text-center font-mono text-3xl font-bold tracking-[0.4em]"
                    style={{
                      borderColor: 'color-mix(in srgb, var(--revka-signal-selected) 35%, transparent)',
                      background: 'color-mix(in srgb, var(--revka-signal-selected) 12%, transparent)',
                      color: 'var(--revka-text-primary)',
                    }}
                  >
                    {pairingCode}
                  </div>
                  <p className="mt-3 text-xs leading-5" style={{ color: 'var(--revka-text-secondary)' }}>
                    {t('pairing.code_hint')}
                  </p>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    className="revka-button"
                    onClick={async () => {
                      const ok = await copyToClipboard(pairingCode);
                      setNotice(
                        ok
                          ? { tone: 'success', message: t('pairing.toast.code_copied') }
                          : { tone: 'error', message: t('pairing.toast.copy_failed') },
                      );
                    }}
                  >
                    {t('pairing.copy_code')}
                  </button>
                  <button className="revka-button" onClick={handleInitiate} disabled={initiating}>
                    <RefreshCw className={`h-4 w-4 ${initiating ? 'animate-spin' : ''}`} />
                    {t('pairing.rotate')}
                  </button>
                </div>
              </div>
            </div>
          </Panel>
        ) : (
          <Panel className="p-5">
            <StateMessage
              tone="empty"
              title={t('pairing.no_code_title')}
              description={t('pairing.no_code_desc')}
              action={(
                <button className="revka-button" data-variant="primary" onClick={handleInitiate} disabled={initiating}>
                  <Smartphone className="h-4 w-4" />
                  {initiating ? t('pairing.generating') : t('pairing.pair_new_device')}
                </button>
              )}
            />
          </Panel>
        )}

        {pairingCode ? (
          <Panel className="p-4" variant="utility">
            <div className="revka-kicker">{t('pairing.how_to_scan')}</div>
            <ol className="mt-3 list-inside list-decimal space-y-2 text-xs leading-5" style={{ color: 'var(--revka-text-secondary)' }}>
              <li>{t('pairing.scan_step1')}</li>
              <li>{t('pairing.scan_step2_prefix')}<span style={{ color: 'var(--revka-text-primary)' }}>{t('pairing.scan_step2_action')}</span>{t('pairing.scan_step2_suffix')}</li>
              <li>{t('pairing.scan_step3')}</li>
              <li>{t('pairing.scan_step4')}</li>
            </ol>
          </Panel>
        ) : null}
      </div>

      <Panel className="min-h-0 flex-1 overflow-hidden p-0">
        <div className="flex items-center justify-between border-b px-4 py-3" style={{ borderColor: 'var(--revka-border-soft)' }}>
          <div className="revka-kicker">{tpl('pairing.paired_devices', { count: devices.length })}</div>
        </div>
        {loading ? (
          <div className="flex min-h-[180px] items-center justify-center">
            <StateMessage tone="loading" title={t('pairing.loading_devices')} />
          </div>
        ) : devices.length === 0 ? (
          <div className="p-4">
            <StateMessage tone="empty" title={t('pairing.no_devices_title')} description={t('pairing.no_devices_desc')} />
          </div>
        ) : (
          <div className="overflow-auto">
            <table className="revka-table w-full">
              <thead>
                <tr>
                  <th>{t('pairing.col.name')}</th>
                  <th>{t('pairing.col.type')}</th>
                  <th>{t('pairing.col.paired')}</th>
                  <th>{t('pairing.col.last_seen')}</th>
                  <th>{t('pairing.col.ip')}</th>
                  <th className="text-right">{t('pairing.col.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {devices.map((device) => (
                  <tr key={device.id}>
                    <td className="text-sm font-medium" style={{ color: 'var(--revka-text-primary)' }}>
                      {device.name || t('pairing.unnamed')}
                    </td>
                    <td style={{ color: 'var(--revka-text-secondary)' }}>{device.device_type || t('pairing.unknown')}</td>
                    <td className="text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                      {new Date(device.paired_at).toLocaleDateString()}
                    </td>
                    <td className="text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                      {new Date(device.last_seen).toLocaleString()}
                    </td>
                    <td className="font-mono text-xs" style={{ color: 'var(--revka-text-secondary)' }}>
                      {device.ip_address || '—'}
                    </td>
                    <td className="text-right">
                      {confirmDelete === device.id ? (
                        <div className="inline-flex items-center gap-2">
                          <span className="text-xs" style={{ color: 'var(--revka-status-danger)' }}>{t('pairing.revoke')}</span>
                          <button className="text-xs font-semibold" style={{ color: 'var(--revka-status-danger)' }} onClick={() => handleRevoke(device.id)}>{t('pairing.yes')}</button>
                          <button className="text-xs font-semibold" style={{ color: 'var(--revka-text-secondary)' }} onClick={() => setConfirmDelete(null)}>{t('pairing.no')}</button>
                        </div>
                      ) : (
                        <button className="revka-button" onClick={() => setConfirmDelete(device.id)} title={t('pairing.revoke_title')}>
                          <Trash2 className="h-4 w-4" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
