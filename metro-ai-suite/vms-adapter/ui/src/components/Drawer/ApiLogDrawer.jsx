import { useState } from 'react';
import { Code2, ChevronUp } from 'lucide-react';
import { t } from '@/utils/i18n';

const METHOD_CLS = {
  post: 'vms-method-post',
  get:  'vms-method-get',
  cmd:  'vms-method-cmd',
};

export default function ApiLogDrawer({ entries = [] }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="vms-api-drawer font-mono-vms">
      {/* Toggle bar */}
      <button className="vms-api-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <Code2 size={13} className="shrink-0" />
        <span>{t('apiLogTitle')}</span>
        <span className="vms-api-count">
          {entries.length} {t('apiLogCalls')}
        </span>
        <ChevronUp size={14} className={`ml-auto transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>

      {/* Entries */}
      <div className={`vms-api-content ${open ? 'vms-api-content-open' : ''}`}>
        {entries.map((e, i) => (
          <div key={i} className="vms-api-entry">
            <span className="vms-api-time">{e.time}</span>
            <span className={METHOD_CLS[e.method.toLowerCase()] ?? 'vms-method-default'}>{e.method}</span>
            <span className="vms-api-path">{e.path}</span>
            <span className={e.statusCode < 400 ? 'vms-api-status-ok' : 'vms-api-status-err'}>{e.statusCode}</span>
            <span className="vms-api-note">{e.note}</span>
          </div>
        ))}
        {entries.length === 0 && (
          <div className="px-5 py-3 text-[0.73rem] text-white/30">{t('apiLogEmpty')}</div>
        )}
      </div>
    </div>
  );
}
