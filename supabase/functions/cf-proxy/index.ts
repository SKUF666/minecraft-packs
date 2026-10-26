// Portalis: посредник к API CurseForge. Ключ CurseForge хранится только здесь, в секрете CF_API_KEY
// (Supabase → Edge Functions → Secrets), и в программу больше не кладётся.
// Пропускаем только то, что нужно Portalis: поиск, проект, его файлы и описание, пачки проектов и файлов.
// Вызов: GET/POST <проект>/functions/v1/cf-proxy?path=mods/search&gameId=432&... с заголовками apikey и
// Authorization: Bearer <публичный ключ Supabase> (функция проверяет его сама, как и всё в Supabase).

const CF = "https://api.curseforge.com/v1/";
const KEY = Deno.env.get("CF_API_KEY") ?? "";
const GAME = "432"; // Minecraft
const ALLOW_GET = /^mods\/(search|\d{1,10}(\/files|\/description)?)$/;
const ALLOW_POST = /^mods(\/files)?$/;
const PER_MINUTE = 120; // запросов в минуту с одного адреса (на этот экземпляр функции)
const hits = new Map<string, number[]>();

function reply(status: number, body: unknown) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function limited(ip: string): boolean {
  const now = Date.now();
  const recent = (hits.get(ip) ?? []).filter((t) => now - t < 60_000);
  recent.push(now);
  hits.set(ip, recent);
  if (hits.size > 5000) hits.clear();
  return recent.length > PER_MINUTE;
}

function idList(v: unknown): number[] | null {
  if (!Array.isArray(v) || v.length === 0 || v.length > 50) return null;
  const out = v.map((x) => Number(x));
  return out.every((x) => Number.isInteger(x) && x > 0 && x < 1e10) ? out : null;
}

Deno.serve(async (req) => {
  if (!KEY) return reply(503, { error: "CF_API_KEY is not set" });
  const ip = (req.headers.get("x-forwarded-for") ?? "?").split(",")[0].trim();
  if (limited(ip)) return reply(429, { error: "too many requests" });

  const url = new URL(req.url);
  const path = (url.searchParams.get("path") ?? "").replace(/^\/+/, "");
  const q = new URLSearchParams(url.searchParams);
  q.delete("path");
  const headers = { "x-api-key": KEY, "Accept": "application/json", "User-Agent": "Portalis-proxy" };

  let res: Response;
  if (req.method === "GET") {
    if (!ALLOW_GET.test(path)) return reply(403, { error: "path not allowed" });
    if (path === "mods/search" && q.get("gameId") !== GAME) return reply(403, { error: "only Minecraft" });
    const qs = q.toString();
    res = await fetch(CF + path + (qs ? "?" + qs : ""), { headers });
  } else if (req.method === "POST") {
    if (!ALLOW_POST.test(path)) return reply(403, { error: "path not allowed" });
    let body: Record<string, unknown>;
    try {
      body = await req.json();
    } catch {
      return reply(400, { error: "bad json" });
    }
    const field = path === "mods" ? "modIds" : "fileIds";
    const ids = idList(body?.[field]);
    if (!ids) return reply(400, { error: field + ": 1..50 numbers" });
    res = await fetch(CF + path, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: ids }),
    });
  } else {
    return reply(405, { error: "method not allowed" });
  }
  return reply(res.status, await res.text());
});
