import { memo, useCallback, useState, type ReactNode } from 'react';
import { Check, Copy } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { copyToClipboard } from '@/construct/lib/clipboard';
import { useT } from '@/construct/hooks/useT';

function textFromNode(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textFromNode).join('');
  return '';
}

function CopyableCodeBlock({ code, language }: { code: string; language?: string }) {
  const [copied, setCopied] = useState(false);
  const { t } = useT();

  const onCopy = useCallback(async () => {
    if (!(await copyToClipboard(code))) return;
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  }, [code]);

  return (
    <div className="group/code relative my-2 overflow-hidden rounded-md border" style={{ borderColor: 'var(--construct-border-soft)', background: 'var(--construct-bg-base)' }}>
      <div className="flex items-center justify-between border-b px-2 py-1" style={{ borderColor: 'var(--construct-border-soft)', color: 'var(--construct-text-faint)' }}>
        <span className="font-mono text-[10px] uppercase">{language || t('agent.code_language_fallback')}</span>
        <button
          type="button"
          onClick={onCopy}
          aria-label={copied ? t('agent.copied_code') : t('agent.copy_code')}
          title={copied ? t('agent.copied') : t('agent.copy_code')}
          className="inline-flex h-5 items-center gap-1 rounded px-1.5 font-mono text-[10px] transition-colors hover:bg-white/5 focus:outline-none focus-visible:ring-1 focus-visible:ring-current"
          style={{ color: copied ? 'var(--construct-status-success)' : 'var(--construct-text-muted)' }}
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          {copied ? t('agent.copied') : t('agent.copy')}
        </button>
      </div>
      <pre className="m-0 max-h-[28rem] overflow-auto p-3 font-mono text-[0.86em] leading-6" style={{ color: 'var(--construct-text-secondary)', textShadow: 'none' }}>
        <code>{code}</code>
      </pre>
    </div>
  );
}

function MarkdownMessage({
  content,
  color,
  textShadow,
}: {
  content: string;
  color?: string;
  textShadow?: string;
}) {
  return (
    <div className="chat-markdown min-w-0 break-words leading-relaxed" style={{ color, textShadow }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre({ children }) {
            return <>{children}</>;
          },
          code({ children, className, node: _node, ...props }) {
            const text = textFromNode(children).replace(/\n$/, '');
            const language = /language-([\w-]+)/.exec(className ?? '')?.[1];
            const isBlock = !!language || text.includes('\n');
            if (isBlock) {
              return <CopyableCodeBlock code={text} language={language} />;
            }
            return (
              <code
                {...props}
                className={className}
                style={{
                  background: 'color-mix(in srgb, var(--construct-bg-surface) 75%, transparent)',
                  color: 'var(--construct-text-secondary)',
                  textShadow: 'none',
                }}
              >
                {children}
              </code>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MarkdownMessage);
