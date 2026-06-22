import type { ConfirmResponse } from '../types/preview';
import { Button } from '@/components/ui/button';

interface Props { result: ConfirmResponse; onStartOver: () => void; }

export default function ProcessingResult({ result, onStartOver }: Props) {
  const isOk = result.ok;

  return (
    <div className="max-w-lg mx-auto">
      {/* Glass card */}
      <div className="glass-card rounded-xl overflow-hidden sakura-shadow">
        {/* Header */}
        <div className={`px-6 py-4 text-center ${isOk ? 'bg-accent/15' : 'bg-destructive/10'}`}>
          <h2 className={`text-lg font-semibold ${isOk ? 'text-accent-foreground' : 'text-destructive'}`}>
            {isOk ? '✅ 处理完成' : '❌ 处理失败'}
          </h2>
        </div>

        {/* Stats */}
        <div className="p-6 space-y-4">
          {isOk ? (
            <div className="flex justify-center gap-8">
              <div className="text-center">
                <div className="text-3xl font-bold text-primary">{result.nfoGenerated}</div>
                <div className="text-xs text-muted-foreground mt-1">NFO 文件</div>
              </div>
              <div className="text-center">
                <div className="text-3xl font-bold text-secondary">{result.imagesDownloaded}</div>
                <div className="text-xs text-muted-foreground mt-1">图片</div>
              </div>
              <div className="text-center">
                <div className="text-3xl font-bold text-accent">{result.filesRenamed}</div>
                <div className="text-xs text-muted-foreground mt-1">重命名</div>
              </div>
            </div>
          ) : (
            <div className="text-destructive text-sm">
              <p>{result.error || '发生未知错误。'}</p>
              {result.nfoGenerated > 0 && (
                <p className="text-muted-foreground mt-2">
                  部分完成：在出错前已生成 {result.nfoGenerated} 个 NFO 文件。
                </p>
              )}
            </div>
          )}

          {result.showDirName && (
            <p className="text-xs text-muted-foreground bg-muted/50 px-3 py-1.5 rounded-lg">
              输出目录：<code className="text-foreground font-medium">{result.showDirName}</code>
            </p>
          )}

          <Button className="w-full shadow-md shadow-primary/10" onClick={onStartOver}>
            重新开始
          </Button>
        </div>
      </div>
    </div>
  );
}
