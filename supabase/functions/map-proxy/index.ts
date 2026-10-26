// Portalis: скачивание файла карты через сервер - когда у игрока сайт с файлом не открывается напрямую
// (блокировка у провайдера, Cloudflare не пускает программы, хостинг требует браузер).
// Только чтение и только файлы карт с известных хостингов: страница или файл не сохраняются, а сразу передаются игроку.
// Вызов: GET <проект>/functions/v1/map-proxy?url=<ссылка на файл со страницы карты>&ref=<страница карты>
// с заголовками apikey и Authorization: Bearer <публичный ключ Supabase>; заголовок Range передаётся дальше -
// программа качает кусками (у функции ограничено время одного запроса) и докачивает после обрыва.

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";
const ALLOW = [
  /^(www\.)?minecraft-inside\.ru$/,
  /^dl\.minecraftmaps\.com$/,
  /^(edge|mediafilez|media)\.forgecdn\.net$/,
  /^drive\.usercontent\.google\.com$/,
  /^(drive|docs)\.google\.com$/,
  /^(disk\.yandex\.(ru|com)|yadi\.sk)$/,
  /^downloader\.disk\.yandex\.ru$/,
  /(^|\.)disk\.yandex\.net$/,
  /(^|\.)storage\.yandex\.net$/,
  /^(www\.)?dropbox\.com$/,
  /(^|\.)dropboxusercontent\.com$/,
  /^(www\.)?mediafire\.com$/,
  /^download\d*\.mediafire\.com$/,
];
const MAX_BYTES = 600 * 1024 * 1024; // больше карт не бывает
const PER_MINUTE = 60; // запросов (кусков) в минуту с одного адреса
const hits = new Map<string, number[]>();

function reply(status: number, error: string) {
  return new Response(JSON.stringify({ error }), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function allowed(u: URL): boolean {
  return u.protocol === "https:" && ALLOW.some((rx) => rx.test(u.hostname.toLowerCase()));
}

function limited(ip: string): boolean {
  const now = Date.now();
  const recent = (hits.get(ip) ?? []).filter((t) => now - t < 60_000);
  recent.push(now);
  hits.set(ip, recent);
  if (hits.size > 5000) hits.clear();
  return recent.length > PER_MINUTE;
}

// Страница хостинга -> прямая ссылка на файл (как direct_link в программе)
async function resolve(u: URL): Promise<URL | null> {
  const host = u.hostname.toLowerCase().replace(/^www\./, "");
  if (host === "drive.google.com" || host === "docs.google.com") {
    const m = u.pathname.match(/\/d\/([\w-]{10,})/) ?? u.search.match(/[?&]id=([\w-]{10,})/);
    return m ? new URL(`https://drive.usercontent.google.com/download?id=${m[1]}&export=download&confirm=t`) : null;
  }
  if (host === "disk.yandex.ru" || host === "disk.yandex.com" || host === "yadi.sk") {
    const r = await fetch("https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key=" +
      encodeURIComponent(u.toString()));
    if (!r.ok) return null;
    const j = await r.json();
    return j?.href ? new URL(j.href) : null;
  }
  if (host === "dropbox.com") {
    const d = new URL(u.toString());
    d.searchParams.set("dl", "1");
    return d;
  }
  if (host === "mediafire.com") {
    const r = await fetch(u.toString(), { headers: { "User-Agent": UA } });
    const m = (await r.text()).match(/href="(https?:\/\/download[^"]+mediafire\.com\/[^"]+)"/);
    return m ? new URL(m[1]) : null;
  }
  return u;
}

Deno.serve(async (req) => {
  if (req.method !== "GET") return reply(405, "method not allowed");
  const ip = (req.headers.get("x-forwarded-for") ?? "?").split(",")[0].trim();
  if (limited(ip)) return reply(429, "too many requests");
  const q = new URL(req.url).searchParams;
  let target: URL | null;
  try {
    target = new URL(q.get("url") ?? "");
  } catch {
    return reply(400, "bad url");
  }
  if (!allowed(target)) return reply(403, "host not allowed");
  let ref = "";
  try {
    const r = new URL(q.get("ref") ?? "");
    if (allowed(r) || r.hostname.endsWith("minecraftmaps.com") || r.hostname.endsWith("curseforge.com")) ref = r.toString();
  } catch { /* без страницы */ }
  try {
    target = await resolve(target);
  } catch {
    target = null;
  }
  if (!target || !allowed(target)) return reply(502, "file link not found");

  const headers: Record<string, string> = { "User-Agent": UA, "Accept": "*/*" };
  const range = req.headers.get("range");
  if (range && /^bytes=\d+-\d*$/.test(range)) headers["Range"] = range;
  let res: Response | null = null;
  for (let hop = 0; hop < 7; hop++) { // переадресации вручную: каждый шаг - только на разрешённый хостинг
    const h = { ...headers };
    if (ref && /minecraft-inside\.ru$/.test(target.hostname)) h["Referer"] = ref; // Google Диску Referer не нужен
    res = await fetch(target.toString(), { headers: h, redirect: "manual" });
    const loc = res.headers.get("location");
    if (res.status >= 300 && res.status < 400 && loc) {
      await res.body?.cancel();
      target = new URL(loc, target);
      if (!allowed(target)) return reply(403, "redirect to " + target.hostname + " not allowed");
      continue;
    }
    break;
  }
  if (!res || !(res.status === 200 || res.status === 206)) {
    const srv = res?.headers.get("server") ?? "";
    const peek = res ? (await res.text().catch(() => "")).replace(/\s+/g, " ").slice(0, 80) : "";
    return reply(502, `source answered ${res?.status ?? "nothing"} (${target.hostname}${srv ? ", " + srv : ""}) ${peek}`);
  }
  const type = res.headers.get("content-type") ?? "application/octet-stream";
  if (type.includes("text/html")) { // вместо файла страница (проверка на вирусы, вход, капча) - это не карта
    await res.body?.cancel();
    return reply(502, "source returned a page, not a file");
  }
  const len = Number(res.headers.get("content-length") ?? "0");
  const total = Number((res.headers.get("content-range") ?? "").split("/")[1] ?? "0");
  if (len > MAX_BYTES || total > MAX_BYTES) {
    await res.body?.cancel();
    return reply(413, "file is too big");
  }
  const out = new Headers({ "content-type": type, "x-source-host": target.hostname });
  for (const k of ["content-length", "content-range", "accept-ranges", "content-disposition"]) {
    const v = res.headers.get(k);
    if (v) out.set(k, v);
  }
  return new Response(res.body, { status: res.status, headers: out });
});
