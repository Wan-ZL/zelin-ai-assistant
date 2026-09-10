// vitest setupFiles（vite.config.ts `test.setupFiles`）——每个判例文件的 jsdom 环境建好之后、判例代码跑之前执行。
//
// 防腐 #7：unit / behaviour 判例禁真网络。jsdom 不实现 fetch，vitest 把 Node 的真 `fetch` 挂在 globalThis 上，
// 于是任何没被 `vi.mock("./api")` 盖住的 api.ts 调用都会真的对 `http://localhost:3000/api/…`（jsdom 缺省 baseURI）
// 开 socket：CI 上 ECONNREFUSED 被 store 静默吞掉、判例照绿，本机若有 dev server 在 :3000 听就变慢 / 回 HTML。
// 这里把它换成**不开 socket、立刻拒绝**的桩，错误类型与断网同一条路（TypeError → api.ts 的 READ_FAILED /
// `browser-network`），既有判例的行为一个字不变，只是再也不碰网卡。需要真响应形状的判例照旧
// `vi.stubGlobal("fetch", …)` 盖过本桩（`vi.unstubAllGlobals()` 还原回来的就是本桩）。
// 判例：vitest.setup.test.ts。

const describeInput = (input: RequestInfo | URL): string =>
  typeof input === "string" ? input : input instanceof URL ? input.href : input.url;

const noNetworkFetch: typeof fetch = (input) =>
  Promise.reject(new TypeError(
    `fetch blocked in vitest (no network in unit tests): ${describeInput(input)} — ` +
      'mock the api.ts function (vi.mock) or vi.stubGlobal("fetch", …)',
  ));

globalThis.fetch = noNetworkFetch;
