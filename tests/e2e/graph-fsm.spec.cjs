const { test: base, expect } = require('../../strategies/_common/graph/ui/node_modules/@playwright/test');

// 真 server（--fixture）＋真瀏覽器；不 mock 任何 API。形式化驗證檢視必須唯讀、只連 loopback、不碰 ledger。
const test = base.extend({
  page: async ({ page }, use) => {
    const external = [];
    const errors = [];
    page.on('request', request => {
      if (new URL(request.url()).origin !== 'http://127.0.0.1:19102') external.push('external request');
    });
    page.on('pageerror', () => errors.push('browser error'));
    page.on('console', message => {
      if (message.type() === 'error' && /content security policy|maximum update depth/i.test(message.text())) errors.push('policy error');
    });
    await use(page);
    expect(external, 'runtime must only contact its loopback origin').toEqual([]);
    expect(errors).toEqual([]);
  },
});

const OVERPROMISE = /已證明|證明正確|保證安全|已驗證策略/;
const BACKEND = /vllm|供應商|\bgpu\b|qwen|deepseek|nvidia/i;

test('F1 /api/fsm serves fixture atlas plus IR-only fallback for the loaded strategy, read-only', async ({ page }) => {
  const before = await (await page.request.get('/api/ledger')).json();
  const response = await page.request.get('/api/fsm');
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  expect(body.source).toBe('fixture');
  expect(body.current).toBe('tsmom_tx_mtx');
  const ids = body.targets.map(t => t.id);
  expect(ids).toEqual(expect.arrayContaining(['fsm_demo', 'tsmom_tx_mtx']));
  const ir = body.targets.find(t => t.id === 'tsmom_tx_mtx');
  expect(ir.status).toBe('unchecked');
  expect(ir.invariants.every(i => i.verdict === 'unchecked')).toBeTruthy();
  const post = await page.request.post('/api/fsm', { data: {} });
  expect(post.ok()).toBeFalsy();
  const after = await (await page.request.get('/api/ledger')).json();
  expect(after.selection_n).toBe(before.selection_n);
  expect(JSON.stringify(body)).not.toMatch(/[A-Za-z]:\\|\/home\//);
});

test('F2 toolbar opens the panel: graph, invariants with origin/enforced, cfg results, plain-language counterexample', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('fsm').click();
  const panel = page.getByTestId('fsm-panel');
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId('fsm-disclaimer')).toContainText('不是策略賺不賺錢');
  // 目前載入的是 tsmom：只有狀態機描述，沒有任何規則被檢查，也不得出現「未被違反」綠色徽章
  await expect(panel.getByTestId('fsm-title')).toContainText('波動率目標時間序列動能');
  await expect(panel.getByTestId('fsm-node')).not.toHaveCount(0);
  await expect(panel.getByTestId('fsm-invariants')).toContainText('未檢查');
  await expect(panel.locator('.fsm-badge.ok')).toHaveCount(0);
  // 切到示範資料
  await panel.getByRole('tab').filter({ hasText: '示範策略' }).click();
  await expect(panel.getByTestId('fsm-source')).toContainText('測試資料');
  await expect(panel.getByTestId('fsm-invariants')).toContainText('交易員提出');
  await expect(panel.getByTestId('fsm-invariants')).toContainText('程式碼沒有實作');
  await expect(panel.getByTestId('fsm-invariants').locator('.fsm-badge.ok')).toContainText('有限模型下不變量未被違反');
  await expect(panel.getByTestId('fsm-cfgs')).toContainText('stress:INV_MAX_TRADE_LOSS');
  await expect(panel.getByTestId('fsm-cfgs')).toContainText('發現反例');
  const cex = panel.getByTestId('fsm-cex');
  await expect(cex).toContainText('【這個反例用到的模型數字與來源】');
  await expect(cex).toContainText('【它代表什麼、不代表什麼】');
  await expect(panel.getByTestId('fsm-cex-incomplete')).toHaveCount(0);
  const text = await panel.innerText();
  expect(text).not.toMatch(OVERPROMISE);
  expect(text).not.toMatch(BACKEND);
  await panel.getByTestId('fsm-close').click();
  await expect(panel).toHaveCount(0);
});

test('F3 panel stays inside the viewport at desktop and narrow widths', async ({ page }) => {
  await page.goto('/');
  for (const width of [1280, 760]) {
    await page.setViewportSize({ width, height: 800 });
    await page.getByTestId('fsm').click();
    const panel = page.getByTestId('fsm-panel');
    await expect(panel.getByTestId('fsm-graph')).toBeVisible();
    const fits = await panel.evaluate(el => {
      const r = el.getBoundingClientRect();
      return r.left >= 0 && r.right <= innerWidth + 1 && el.scrollWidth <= el.clientWidth + 24;
    });
    expect(fits).toBeTruthy();
    await panel.getByTestId('fsm-close').click();
  }
});

test('F4 embed view (?view=fsm) is read-only: no canvas, no session, only GET /api/fsm', async ({ page }) => {
  const calls = [];
  page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/api/')) calls.push(`${r.method()} ${new URL(r.url()).pathname}`); });
  const response = await page.goto('/?view=fsm');
  const csp = response.headers()['content-security-policy'] || '';
  expect(csp).toContain("frame-ancestors 'none'");
  expect(response.headers()['x-frame-options']).toBe('DENY');
  await expect(page.getByTestId('fsm-embed')).toBeVisible();
  await expect(page.getByTestId('fsm-title')).toBeVisible();
  await expect(page.locator('.react-flow')).toHaveCount(0);
  await expect(page.getByTestId('run')).toHaveCount(0);
  expect([...new Set(calls)]).toEqual(['GET /api/fsm']);
});

test('F5 fixture isolation: opening the panel never changes the ledger or the saved graph', async ({ page }) => {
  const read = async () => ({
    ledger: await (await page.request.get('/api/ledger')).json(),
    doc: (await (await page.request.get('/api/graph')).json()).graph_hash,
  });
  const before = await read();
  await page.goto('/');
  await page.getByTestId('fsm').click();
  await expect(page.getByTestId('fsm-panel')).toBeVisible();
  await page.getByTestId('fsm-close').click();
  expect(await read()).toEqual(before);
});
