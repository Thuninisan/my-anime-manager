/** Matching logic: parsed_files → search_results → episode_data → table.

   1. parsed_file.show_name → search_results[key]
   2. bangumi.id → episode_data.bangumi[id].episodes (sorted by sort)
   3. parsed_file.episode → positional index → bangumi episode
   4. bangumi ep .name → fuzzy match TMDB episodes across all seasons
   5. Return TMDB season + episode
*/

interface ParsedFile {
  file_name: string;
  torrent_path: string;
  show_name: string;
  season: number;
  episode: number;
}

interface SearchEntry {
  tmdb: { id: number; name: string } | null;
  bangumi: { id: number; name: string; name_cn?: string } | null;
}

interface TmdbEpisode {
  epNum: number;
  tmdbId: number;
  name: string;
  name_cn?: string;
}

interface TmdbSeason {
  name: string;
  episodes: TmdbEpisode[];
}

interface BgmEpisode {
  sort: number;
  id: number;
  name: string;
  name_cn?: string;
}

interface BgmEntry {
  name: string;
  episodes: BgmEpisode[];
}

interface MatchRow {
  file_name: string;
  show_name: string;
  src_season: number;
  src_episode: number;
  bgm_entry: string;
  bgm_sort: number | null;
  bgm_ep_name: string;
  bgm_ep_name_cn: string;
  tmdb_season: number | null;
  tmdb_ep: number | null;
  tmdb_ep_name: string;
  matched: boolean;
}

/**
 * Normalise a string for fuzzy comparison: full-width → half-width
 * for ASCII-range characters (e.g. "！" → "!", "＂" → "\""),
 * then trim and lowercase.
 */
function normalise(s: string): string {
  return s
    .normalize("NFKC")         // Unicode canonical + compatibility composition
    .replace(/[！-～]/g, (ch) =>
      String.fromCharCode(ch.charCodeAt(0) - 0xFF01 + 0x21),
    )
    .replace(/　/g, " ")       // full-width space → half-width space
    .trim()
    .toLowerCase();
}

/** Character-level Dice coefficient in [0, 1].  Treats each string as a
 *  bag of characters (after normalisation).  Higher = more similar. */
function charSimilarity(a: string, b: string): number {
  const sa = new Set(a);
  const sb = new Set(b);
  if (sa.size === 0 && sb.size === 0) return 1;
  let overlap = 0;
  for (const ch of sa) {
    if (sb.has(ch)) overlap++;
  }
  return (2 * overlap) / (sa.size + sb.size);
}

function fuzzyMatchTmdb(
  bgmName: string,
  bgmNameCn: string,
  tmdbSeasons: Record<string, TmdbSeason>,
): { season: number; epNum: number; name: string; score?: number } | null {
  const bgmNorm = normalise(bgmName);
  const bgmCnNorm = normalise(bgmNameCn);

  // Build flat candidate list
  const allEps: { season: number; ep: TmdbEpisode }[] = [];
  for (const [skey, sdata] of Object.entries(tmdbSeasons)) {
    for (const ep of sdata.episodes) {
      allEps.push({ season: Number(skey), ep });
    }
  }

  // Round 1: exact match (name or name_cn)
  for (const { season, ep } of allEps) {
    const names = [ep.name];
    if (ep.name_cn) names.push(ep.name_cn);
    for (const n of names) {
      const nn = normalise(n);
      if (nn === bgmNorm || (bgmCnNorm && nn === bgmCnNorm)) {
        return { season, epNum: ep.epNum, name: ep.name };
      }
    }
  }

  // Round 2: contains/substring match
  for (const { season, ep } of allEps) {
    const names = [ep.name];
    if (ep.name_cn) names.push(ep.name_cn);
    for (const n of names) {
      const nn = normalise(n);
      if ((nn && bgmNorm && (nn.includes(bgmNorm) || bgmNorm.includes(nn))) ||
          (nn && bgmCnNorm && (nn.includes(bgmCnNorm) || bgmCnNorm.includes(nn)))) {
        return { season, epNum: ep.epNum, name: ep.name };
      }
    }
  }

  // Round 3: character-level Dice similarity (fallback for variant kanji)
  const MIN_SIMILARITY = 0.55;
  let best: { season: number; epNum: number; name: string; score: number } | null = null;
  for (const { season, ep } of allEps) {
    const names = [ep.name];
    if (ep.name_cn) names.push(ep.name_cn);
    for (const n of names) {
      const nn = normalise(n);
      const scoreA = charSimilarity(bgmNorm, nn);
      const scoreB = bgmCnNorm ? charSimilarity(bgmCnNorm, nn) : 0;
      const score = Math.max(scoreA, scoreB);
      if (score > (best?.score ?? 0)) {
        best = { season, epNum: ep.epNum, name: ep.name, score };
      }
    }
  }
  if (best && best.score >= MIN_SIMILARITY) {
    return best;
  }

  return null;
}

export function computeMatches(data: any): MatchRow[] {
  const parsedFiles: ParsedFile[] = data.parsed_files || [];
  const searchResults: Record<string, SearchEntry> = data.search_results || {};
  const episodeData = data.episode_data || { tmdb: {}, bangumi: {} };

  return parsedFiles.map((pf) => {
    const searchEntry = searchResults[pf.show_name];

    const tmdbId = searchEntry?.tmdb?.id;
    const bangumiId = searchEntry?.bangumi?.id;

    const tmdbSeasons: Record<string, TmdbSeason> =
      (tmdbId && episodeData.tmdb?.[String(tmdbId)]) || {};
    const bgmEntry: BgmEntry | undefined =
      (bangumiId && episodeData.bangumi?.[String(bangumiId)]) || undefined;

    // Match by sort first, fall back to positional index
    const bgmEps = bgmEntry?.episodes || [];
    let bgmEp = bgmEps.find((ep) => ep.sort === pf.episode) ?? null;
    if (!bgmEp && pf.episode > 0 && pf.episode <= bgmEps.length) {
      bgmEp = bgmEps[pf.episode - 1];
    }

    // Fuzzy match BGM name → TMDB (name + name_cn for better coverage)
    const tmdbMatch = bgmEp?.name
      ? fuzzyMatchTmdb(bgmEp.name, bgmEp.name_cn || "", tmdbSeasons)
      : null;

    return {
      file_name: pf.file_name,
      show_name: pf.show_name,
      src_season: pf.season,
      src_episode: pf.episode,
      bgm_entry: bgmEntry?.name || (bangumiId ? `ID ${bangumiId}` : '-'),
      bgm_sort: bgmEp?.sort ?? null,
      bgm_ep_name: bgmEp?.name || '-',
      bgm_ep_name_cn: bgmEp?.name_cn || '',
      tmdb_season: tmdbMatch?.season ?? null,
      tmdb_ep: tmdbMatch?.epNum ?? null,
      tmdb_ep_name: tmdbMatch?.name || '-',
      matched: tmdbMatch !== null,
    };
  });
}

export default function MatchTable({ data }: { data: any }) {
  const rows = computeMatches(data);

  return (
    <div className="max-w-full mx-auto mt-4 glass-card rounded-xl p-4 overflow-auto max-h-[70vh]">
      <table className="w-full text-xs border-collapse">
        <thead className="sticky top-0 bg-card z-10">
          <tr className="border-b border-border text-muted-foreground">
            <th className="text-left p-1.5 whitespace-nowrap">File</th>
            <th className="text-left p-1.5 whitespace-nowrap">Show</th>
            <th className="text-center p-1.5 w-10">S</th>
            <th className="text-center p-1.5 w-10">E</th>
            <th className="text-left p-1.5 whitespace-nowrap">BGM Entry</th>
            <th className="text-center p-1.5 w-10">BGM#</th>
            <th className="text-left p-1.5">BGM Name</th>
            <th className="text-center p-1.5 w-10">T S</th>
            <th className="text-center p-1.5 w-10">T E</th>
            <th className="text-left p-1.5">TMDB Name</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={i}
              className={`border-b border-border/50 ${
                r.matched ? '' : 'bg-amber-500/5'
              }`}
            >
              <td className="p-1.5 font-mono whitespace-nowrap">{r.file_name}</td>
              <td className="p-1.5 whitespace-nowrap text-muted-foreground">{r.show_name}</td>
              <td className="p-1.5 text-center">{r.src_season}</td>
              <td className="p-1.5 text-center">{r.src_episode}</td>
              <td className="p-1.5 whitespace-nowrap text-muted-foreground max-w-[140px] truncate">{r.bgm_entry}</td>
              <td className="p-1.5 text-center">{r.bgm_sort ?? '-'}</td>
              <td className="p-1.5 max-w-[200px] truncate" title={`${r.bgm_ep_name}${r.bgm_ep_name_cn ? ` / ${r.bgm_ep_name_cn}` : ''}`}>
                {r.bgm_ep_name}
                {r.bgm_ep_name_cn && <span className="text-muted-foreground ml-1">{r.bgm_ep_name_cn}</span>}
              </td>
              <td className={`p-1.5 text-center ${r.matched ? 'text-emerald-400' : 'text-muted-foreground'}`}>
                {r.tmdb_season ?? '-'}
              </td>
              <td className={`p-1.5 text-center ${r.matched ? 'text-emerald-400' : 'text-muted-foreground'}`}>
                {r.tmdb_ep ?? '-'}
              </td>
              <td className={`p-1.5 max-w-[200px] truncate ${r.matched ? '' : 'text-muted-foreground'}`}>
                {r.tmdb_ep_name}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
