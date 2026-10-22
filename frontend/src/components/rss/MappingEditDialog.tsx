import { useEffect, useRef, useState } from 'react';
import * as rssApi from '@/api/rssApi';
import type { BangumiMapping, MappingFields, MappingUpdate, MappingChangePreview, MappingChangeTask } from '@/types/mapping';
import type { CatalogSeason } from '@/types/episode';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DialogRoot, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter } from '@/components/ui/dialog';
import MappingChangeConfirmDialog from './MappingChangeConfirmDialog';

interface Props {
  bangumiId: number;
  name: string;
  isMovie: boolean;
  onClose: () => void;
  onSaved: (mapping: BangumiMapping, previous: BangumiMapping, close?: boolean) => void;
}

type Form = Record<keyof MappingFields, string>;
const fields: (keyof MappingFields)[] = ['mikan_id', 'tmdb_id', 'tmdb_season', 'tmdb_ep_offset', 'tvdb_id', 'tvdb_season', 'tvdb_ep_offset'];
const emptyForm = Object.fromEntries(fields.map(field => [field, ''])) as Form;
const selectClass = 'w-full rounded-md border border-input bg-background px-3 py-2 text-sm';

export default function MappingEditDialog({ bangumiId, name, isMovie, onClose, onSaved }: Props) {
  const [mapping, setMapping] = useState<BangumiMapping | null>(null);
  const [form, setForm] = useState<Form>(emptyForm);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<MappingChangePreview | null>(null);
  const [task, setTask] = useState<MappingChangeTask | null>(null);
  const taskId = task?.task_id;
  const taskStatus = task?.status;
  const taskKind = task?.kind;
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [query, setQuery] = useState(name);
  const [results, setResults] = useState<rssApi.TmdbSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [selectedTitle, setSelectedTitle] = useState('');
  const [seasonResult, setSeasonResult] = useState<{ id: string; seasons: Record<string, CatalogSeason>; error: string } | null>(null);
  const searchState = useRef({ active: true, request: 0 });
  const catalogId = !isMovie && /^[1-9]\d*$/.test(form.tmdb_id) && Number.isSafeInteger(Number(form.tmdb_id)) ? form.tmdb_id : '';
  const seasons = seasonResult?.id === catalogId ? seasonResult.seasons : {};
  const seasonError = seasonResult?.id === catalogId ? seasonResult.error : '';
  const seasonLoading = !!catalogId && seasonResult?.id !== catalogId;

  useEffect(() => {
    const state = searchState.current;
    state.active = true;
    return () => { state.active = false; ++state.request; };
  }, []);

  useEffect(() => {
    let active = true;
    rssApi.getBangumiMapping(bangumiId).then(value => {
      if (!active) return;
      setMapping(value);
      setForm(Object.fromEntries(fields.map(field => [field, value[field] == null ? '' : String(value[field])])) as Form);
    }).catch(cause => {
      if (active) setError(cause instanceof Error ? cause.message : '读取映射失败');
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [bangumiId, reload]);

  useEffect(() => {
    let active = true;
    rssApi.getLatestMappingTask(bangumiId).then(value => {
      if (active && value && (value.status === 'queued' || value.status === 'running' ||
        value.status === 'failed' || value.status === 'partial_failed')) setTask(value);
    }).catch(() => {});
    return () => { active = false; };
  }, [bangumiId]);

  useEffect(() => {
    if (!taskId || (taskStatus !== 'queued' && taskStatus !== 'running')) return;
    let active = true;
    const timer = window.setInterval(() => {
      rssApi.getMappingTask(bangumiId, taskId).then(value => {
        if (!active) return;
        setError('');
        setTask(value);
      }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : '任务状态读取失败'); });
    }, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [taskId, taskStatus, bangumiId]);

  useEffect(() => {
    if (taskStatus !== 'completed' || taskKind !== 'cleanup' || !mapping) return;
    let active = true;
    rssApi.getBangumiMapping(bangumiId).then(saved => {
      if (active) onSaved(saved, mapping);
    }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : '映射读取失败'); });
    return () => { active = false; };
  }, [taskStatus, taskKind, bangumiId, mapping, onSaved]);

  useEffect(() => {
    let active = true;
    if (!catalogId) return;
    // 等待连续输入结束，且只展示当前 ID 对应的目录。
    const timer = window.setTimeout(() => {
      rssApi.getTmdbSeasonMap(Number(catalogId)).then(value => {
        if (active) setSeasonResult({ id: catalogId, seasons: value, error: '' });
      }).catch(cause => {
        if (active) setSeasonResult({ id: catalogId, seasons: {}, error: cause instanceof Error ? cause.message : '季目录加载失败' });
      });
    }, 350);
    return () => { active = false; window.clearTimeout(timer); };
  }, [catalogId]);

  const change = (field: keyof MappingFields, value: string) => {
    setError('');
    setForm(current => {
      const next = { ...current, [field]: value };
      for (const provider of ['tmdb', 'tvdb'] as const) {
        if (field === `${provider}_id` && current[field] !== value) {
          next[`${provider}_season`] = provider === 'tmdb' && value ? '1' : '';
          next[`${provider}_ep_offset`] = '';
        }
        if (field === `${provider}_season` && current[field] !== value) next[`${provider}_ep_offset`] = '';
      }
      return next;
    });
    if (field === 'tmdb_id') setSelectedTitle('');
  };

  const searchTmdb = async () => {
    const state = searchState.current;
    const request = ++state.request;
    setSearching(true);
    setSearchError('');
    setResults([]);
    try {
      const value = await rssApi.searchTmdbShows(query.trim());
      if (state.active && request === state.request) setResults(value);
      if (state.active && request === state.request && value.length === 0) setSearchError('未找到匹配作品，可直接填写 TMDB ID。');
    } catch (cause) {
      if (state.active && request === state.request) setSearchError(cause instanceof Error ? cause.message : '搜索失败');
    } finally {
      if (state.active && request === state.request) setSearching(false);
    }
  };

  const save = async () => {
    if (!mapping || saving) return;
    const changes: MappingUpdate = {};
    for (const field of fields) {
      if (isMovie && field !== 'mikan_id') continue;
      const raw = form[field].trim();
      const value = raw === '' ? null : Number(raw);
      const isOffset = field.endsWith('_ep_offset');
      const isId = field.endsWith('_id');
      if (value !== null && (!/^-?\d+$/.test(raw) || !Number.isSafeInteger(value) || (!isOffset && value < (isId ? 1 : 0)))) {
        setError(`${field}：${isOffset ? '请输入整数，或留空表示未确认' : isId ? '请输入正整数 ID，或留空清除关联' : '请输入非负整数季数，或留空使用默认值'}`);
        return;
      }
      if (value !== mapping[field]) changes[field] = value;
    }
    if (!isMovie) {
      for (const provider of ['tmdb', 'tvdb'] as const) {
        const idChanged = `${provider}_id` in changes;
        const seasonChanged = `${provider}_season` in changes;
        // 新绑定下重新确认的季数和偏移即使等于旧值，也必须显式提交。
        if (idChanged) {
          const raw = form[`${provider}_season`].trim();
          changes[`${provider}_season`] = raw === '' ? null : Number(raw);
        }
        if (idChanged || seasonChanged) {
          const raw = form[`${provider}_ep_offset`].trim();
          changes[`${provider}_ep_offset`] = raw === '' ? null : Number(raw);
        }
      }
    }
    if (Object.keys(changes).length === 0) { onClose(); return; }
    setSaving(true);
    setError('');
    try {
      const impact = await rssApi.previewMappingChange(bangumiId, changes);
      if (impact.kind === 'cleanup') {
        setPreview(impact);
      } else {
        const saved = await rssApi.updateBangumiMapping(bangumiId, changes);
        if (impact.kind === 'nfo_offer') {
          setPreview(impact);
          setMapping(saved);
          onSaved(saved, mapping, false);
        } else onSaved(saved, mapping);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存映射失败');
    } finally {
      setSaving(false);
    }
  };

  const confirmChange = async () => {
    if (!preview || saving) return;
    setSaving(true);
    setError('');
    try {
      const next = preview.kind === 'cleanup'
        ? await rssApi.confirmMappingChange(bangumiId, preview.task_id!)
        : await rssApi.startMappingNfoRebuild(bangumiId, preview.task_id!);
      setTask(next);
      setPreview(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '启动任务失败');
    } finally { setSaving(false); }
  };

  const retryTask = async () => {
    if (!task || saving) return;
    setSaving(true);
    setError('');
    try { setTask(await rssApi.retryMappingTask(bangumiId, task.task_id)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '重试失败'); }
    finally { setSaving(false); }
  };

  const seasonKeys = Object.keys(seasons).sort((a, b) => Number(a) - Number(b));
  const tmdbTitle = selectedTitle || (Number(form.tmdb_id) === mapping?.tmdb_id
    ? mapping.tmdb_series_title_cn || mapping.tmdb_series_title_jp : '');

  return (
    <DialogRoot open onOpenChange={open => { if (!open && !saving && task?.status !== 'running' && task?.status !== 'queued') onClose(); }}>
      <DialogContent className="max-w-2xl max-h-[90vh] flex flex-col" aria-describedby="mapping-description">
        <DialogHeader><DialogTitle>编辑映射</DialogTitle></DialogHeader>
        <DialogBody className="space-y-4 overflow-y-auto">
          <div>
            <p className="font-medium">{name}</p>
            <p className="text-xs text-muted-foreground">Bangumi ID: {bangumiId} · {mapping?.override ? '手动映射' : '平台映射'}</p>
          </div>
          <p id="mapping-description" className="text-xs text-muted-foreground">
            更改 Mikan 映射时，有旧订阅将先确认清理文件与订阅。仅更改 TMDB/TVDB 映射时，保存后可选择重建 NFO。社区同步会保留手动修改。
          </p>
          {(preview || task) && <MappingChangeConfirmDialog preview={preview} task={task} busy={saving}
            onConfirm={() => void confirmChange()}
            onEdit={() => { setTask(null); setPreview(null); setError(''); setLoading(true); setReload(value => value + 1); }}
            onCancel={() => { if (task?.kind === 'nfo' && mapping) onSaved(mapping, mapping); else if (task) onClose(); else if (preview?.kind === 'cleanup') setPreview(null); else onClose(); }}
            onRetry={() => void retryTask()} />}
          {!preview && !task && (loading ? <p role="status">正在读取映射…</p> : mapping && (
            <fieldset disabled={saving} className="space-y-4">
              <section className="rounded-lg border p-4 space-y-2">
                <label htmlFor="mapping-mikan-id" className="text-sm font-medium">Mikan ID</label>
                <div className="flex gap-2">
                  <Input id="mapping-mikan-id" type="number" min="1" step="1" value={form.mikan_id} onChange={e => change('mikan_id', e.target.value)} placeholder="留空清除关联" />
                  <Button variant="outline" onClick={() => change('mikan_id', '')}>清除</Button>
                </div>
              </section>
              {isMovie && <p className="text-sm text-muted-foreground">当前为电影订阅，此处仅支持修改 Mikan 关联，剧集平台映射不适用于电影。</p>}
              <fieldset disabled={isMovie} className="space-y-4 disabled:opacity-50">
                {(['tmdb', 'tvdb'] as const).map(provider => (
                  <section key={provider} className="rounded-lg border p-4 space-y-3">
                    <h3 className="text-sm font-semibold">{provider.toUpperCase()}{provider === 'tmdb' && tmdbTitle ? ` · ${tmdbTitle}` : ''}</h3>
                    <div className="text-sm space-y-1">
                      <label htmlFor={`mapping-${provider}-id`}>剧集 ID</label>
                      <div className="flex gap-2">
                        <Input id={`mapping-${provider}-id`} type="number" min="1" step="1" value={form[`${provider}_id`]} onChange={e => change(`${provider}_id`, e.target.value)} placeholder="留空清除关联" />
                        <Button variant="outline" onClick={() => change(`${provider}_id`, '')}>清除</Button>
                      </div>
                    </div>
                    {provider === 'tmdb' && (
                      <div className="space-y-2">
                        <div className="flex gap-2">
                          <Input aria-label="搜索 TMDB 作品" value={query} onChange={e => setQuery(e.target.value)} placeholder="输入作品名称" onKeyDown={e => { if (e.key === 'Enter' && query.trim() && !searching) { e.preventDefault(); void searchTmdb(); } }} />
                          <Button variant="outline" disabled={searching || !query.trim()} onClick={() => void searchTmdb()}>{searching ? '搜索中…' : '搜索'}</Button>
                        </div>
                        {searchError && <p className="text-xs text-destructive" role="status">{searchError}</p>}
                        {results.length > 0 && <div className="max-h-40 overflow-y-auto rounded border divide-y">
                          {results.map(item => <button key={item.id} type="button" className="block w-full p-2 text-left text-sm hover:bg-muted" onClick={() => { change('tmdb_id', String(item.id)); setSelectedTitle(item.name); setResults([]); }}>
                            {item.name} <span className="text-xs text-muted-foreground">{item.original_name} · {item.first_air_date || '日期未知'} · ID {item.id}</span>
                          </button>)}
                        </div>}
                      </div>
                    )}
                    <fieldset disabled={!form[`${provider}_id`]} className="grid grid-cols-2 gap-3 disabled:opacity-50">
                      <label className="text-sm space-y-1">
                        <span>季数（0 为特别篇）</span>
                        {provider === 'tmdb' && seasonKeys.length > 0 ? (
                          <select className={selectClass} value={form.tmdb_season} onChange={e => change('tmdb_season', e.target.value)}>
                            <option value="">默认第 1 季</option>
                            {form.tmdb_season && !seasonKeys.includes(form.tmdb_season) && <option value={form.tmdb_season}>第 {form.tmdb_season} 季（目录未收录）</option>}
                            {seasonKeys.map(key => <option key={key} value={key}>{seasons[key].name || `第 ${key} 季`}</option>)}
                          </select>
                        ) : <Input type="number" min="0" step="1" value={form[`${provider}_season`]} onChange={e => change(`${provider}_season`, e.target.value)} placeholder={provider === 'tmdb' ? '默认第 1 季' : '未指定'} />}
                      </label>
                      <label className="text-sm space-y-1">
                        <span>集数偏移</span>
                        <Input type="number" step="1" value={form[`${provider}_ep_offset`]} onChange={e => change(`${provider}_ep_offset`, e.target.value)} placeholder="留空未确认，0 表示相同" />
                      </label>
                    </fieldset>
                    {provider === 'tmdb' && seasonLoading && <p className="text-xs text-muted-foreground" role="status">正在读取季目录…</p>}
                    {provider === 'tmdb' && seasonError && <p className="text-xs text-destructive">{seasonError}，可手动输入季数。</p>}
                    <p className="text-xs text-muted-foreground">平台集号 = Bangumi 集号 + 偏移；例如偏移 12 时，第 1 集对应平台第 13 集。更换 ID 或季数后需重新确认偏移。</p>
                  </section>
                ))}
              </fieldset>
            </fieldset>
          ))}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          {!preview && !task && !loading && !mapping && <Button variant="outline" onClick={() => { setLoading(true); setError(''); setReload(value => value + 1); }}>重新读取</Button>}
        </DialogBody>
        {!preview && !task && <DialogFooter>
          <Button variant="outline" disabled={saving} onClick={onClose}>取消</Button>
          <Button disabled={loading || saving || !mapping} onClick={() => void save()}>{saving ? '保存中…' : '保存修改'}</Button>
        </DialogFooter>}
      </DialogContent>
    </DialogRoot>
  );
}
