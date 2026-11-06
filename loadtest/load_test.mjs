// Load test (TEST-13): one representative pilot workflow, end to end,
// with per-phase wall-clock timings.
//
// Generates a synthetic commerce dataset (~3k customers / ~15k orders by
// default, 18 months of history), then measures:
//   upload → mapping → validate → train → score → browse (list + detail)
//   → segment → export → download
//
// Usage: BASE=http://localhost:8000 node loadtest/load_test.mjs
// Env:   CUSTOMERS (default 3000), ORDERS_PER (avg orders per customer, default 5)
// Node >= 18 required (fetch/FormData/Blob).

const BASE = process.env.BASE ?? "http://localhost:8000";
const N_CUSTOMERS = Number(process.env.CUSTOMERS ?? 3000);
const ORDERS_PER = Number(process.env.ORDERS_PER ?? 5);

const phases = [];
async function timed(name, fn) {
  const t0 = performance.now();
  const result = await fn();
  const ms = Math.round(performance.now() - t0);
  phases.push({ name, ms });
  console.log(`  ${name.padEnd(28)} ${ms.toLocaleString()} ms`);
  return result;
}

let TOKEN = "";
let ORG = "";
const headers = () => ({ Authorization: `Bearer ${TOKEN}`, "X-Organization-ID": ORG });

async function call(method, path, body, { form, raw = false } = {}) {
  const h = { ...headers() };
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    h["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(`${BASE}${path}`, { method, headers: h, body: payload });
  const text = await res.text();
  if (raw) return { status: res.status, text, headers: res.headers };
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    /* non-JSON */
  }
  if (!res.ok) {
    throw new Error(`${method} ${path} -> ${res.status}: ${text.slice(0, 200)}`);
  }
  return json;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function pollUntil(descender, isDone, timeoutMs) {
  const start = Date.now();
  let last = null;
  while (Date.now() - start < timeoutMs) {
    last = await descender();
    if (isDone(last)) return last;
    await sleep(1250);
  }
  throw new Error(`${timeoutMs / 1000}s timeout waiting`);
}

// --- synthetic data ---------------------------------------------------------

function mulberry32(seed) {
  let a = seed;
  return () => {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t >>> 7, t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function buildData() {
  const rand = mulberry32(42);
  const start = Date.UTC(2024, 0, 1);
  const last = Date.UTC(2025, 6, 1); // 18 months of history
  const span = last - start;
  const customers = ["customer_id,created_at,country,acquisition_channel,customer_status"];
  const orders = [
    "order_id,customer_id,order_date,order_status,order_amount,currency,discount_amount,shipping_amount,item_count",
  ];
  let orderNo = 1;
  for (let i = 0; i < N_CUSTOMERS; i++) {
    const id = `C${String(i + 1).padStart(5, "0")}`;
    const created = new Date(start + Math.floor(rand() * span * 0.25)); // join in first ~4.5 months
    customers.push(
      [
        id,
        created.toISOString().replace("Z", ""),
        ["US", "DE", "UK", "FR"][i % 4],
        ["google", "facebook", "organic", "email"][i % 4],
        rand() < 0.06 ? "inactive" : "active",
      ].join(",")
    );
    const n = Math.max(1, Math.round(ORDERS_PER * (0.3 + rand() * 1.7)));
    for (let o = 0; o < n; o++) {
      const orderDate = new Date(created.getTime() + rand() * (last - created.getTime()));
      if (orderDate >= last) continue;
      orders.push(
        [
          `O${String(orderNo++).padStart(7, "0")}`,
          id,
          `${orderDate.toISOString().slice(0, 10)} ${orderDate.toISOString().slice(11, 19)}`,
          rand() < 0.85 ? "completed" : rand() < 0.5 ? "refunded" : "returned",
          (10 + rand() * 490).toFixed(2),
          "USD",
          "0.00",
          "10.00",
          String(1 + Math.floor(rand() * 3)),
        ].join(",")
      );
    }
  }
  return { customersCsv: customers.join("\n"), ordersCsv: orders.join("\n") };
}

// --- workflow ----------------------------------------------------------------

async function main() {
  console.log(
    `== CustomerIQ load test ==\n   BASE=${BASE}  customers=${N_CUSTOMERS}  approx orders=${N_CUSTOMERS}x${ORDERS_PER}\n`
  );

  const data = await timed("generate csv (in memory)", () => {
    const d = buildData();
    console.log(
      `     customers.csv ${(d.customersCsv.length / 1e6).toFixed(2)} MB, orders.csv ${(d.ordersCsv.length / 1e6).toFixed(2)} MB`
    );
    return d;
  });

  const email = `loadtest${Date.now()}@test.io`;
  const reg = await call("POST", "/api/v1/auth/register", {
    email,
    password: "Tester1234!",
    full_name: "Load Tester",
    organization_name: "Load Test Org",
  });
  TOKEN = reg.access_token;
  const me = await call("GET", "/api/v1/auth/me");
  ORG = me.organizations[0].id;

  const ds = await call("POST", "/api/v1/datasets", { name: `Load Test ${new Date().toISOString().slice(0, 10)}` });
  const dsId = ds.dataset.id;

  const fileIds = {};
  const uploaded = await timed("upload customers.csv", () =>
    (async () => {
      const form = new FormData();
      form.append("kind", "customers");
      form.append("file", new Blob([data.customersCsv]), "customers.csv");
      return call("POST", `/api/v1/datasets/${dsId}/files`, undefined, { form });
    })()
  );
  fileIds.customers = uploaded.file.id;
  if (!(uploaded.file.headers ?? []).includes("customer_id")) {
    throw new Error(`customers upload parsed wrong headers: ${JSON.stringify(uploaded.file.headers)}`);
  }
  const uploaded2 = await timed("upload orders.csv", () =>
    (async () => {
      const form = new FormData();
      form.append("kind", "orders");
      form.append("file", new Blob([data.ordersCsv]), "orders.csv");
      return call("POST", `/api/v1/datasets/${dsId}/files`, undefined, { form });
    })()
  );
  fileIds.orders = uploaded2.file.id;
  await call("PATCH", `/api/v1/datasets/${dsId}/files/${fileIds.customers}`, {
    mapping: uploaded.suggested_mapping,
  });
  await call("PATCH", `/api/v1/datasets/${dsId}/files/${fileIds.orders}`, {
    mapping: uploaded2.suggested_mapping,
  });

  const ver = await timed("create version + queue validation", () =>
    call("POST", `/api/v1/datasets/${dsId}/versions`, {
      customers_file_id: fileIds.customers,
      orders_file_id: fileIds.orders,
    })
  );
  const verId = ver.version.id;
  await timed("validate 3k customers / ~15k orders", () =>
    pollUntil(
      () => call("GET", `/api/v1/datasets/${dsId}/versions/${verId}`),
      (d) => d.status !== "validating",
      600000
    )
  );
  const verDetail = await call("GET", `/api/v1/datasets/${dsId}/versions/${verId}`);
  if (verDetail.status !== "valid") {
    throw new Error(`version not valid: ${JSON.stringify(verDetail.error_summary).slice(0, 200)}`);
  }

  const train = await timed("queue churn training", () =>
    call("POST", "/api/v1/churn/model-runs", { dataset_version_id: verId })
  );
  await timed("train churn model (3 periods)", () =>
    pollUntil(
      () => call("GET", `/api/v1/churn/model-runs/${train.run.id}`),
      (d) => d.run.status !== "queued" && d.run.status !== "running",
      900000
    )
  );
  const runDetail = await call("GET", `/api/v1/churn/model-runs/${train.run.id}`);
  if (runDetail.run.status !== "succeeded") {
    throw new Error(`training failed: ${(runDetail.run.error ?? "").slice(0, 200)}`);
  }

  const scored = await timed("queue batch scoring", () =>
    call("POST", "/api/v1/churn/scores", { model_run_id: train.run.id })
  );
  const scoreJobId = scored.job?.id ?? scored.job_id;
  await timed("score batch (every eligible customer)", () =>
    pollUntil(
      () => call("GET", `/api/v1/jobs/${scoreJobId}`),
      (j) => j.status !== "queued" && j.status !== "running",
      900000
    )
  );

  // browse-latency probes: repeated reads of the two hottest pages
  const probes = [];
  for (let i = 0; i < 5; i++) {
    let t = performance.now();
    const scores = await call(
      "GET",
      `/api/v1/churn/scores?model_run_id=${train.run.id}&order=desc&page=1&page_size=25`
    );
    probes.push({ name: "GET /churn/scores (page 1)", ms: Math.round(performance.now() - t), total: scores.total });
    if (i === 0) {
      console.log(`     scores population: ${scores.total.toLocaleString()} customers`);
    }
    t = performance.now();
    const cust = await call(
      "GET",
      `/api/v1/customers/${encodeURIComponent(scores.items[0].customer_id)}?dataset_version_id=${verId}`
    );
    probes.push({ name: "GET /customers/{id} (detail)", ms: Math.round(performance.now() - t) });
  }
  for (const p of probes) {
    phases.push({ name: `probe ${p.name}`, ms: p.ms });
  }
  console.log(
    `     scores page latency: ${probes.find((p) => p.name.includes("scores")).ms} ms (first), ` +
      `detail page: ${probes.find((p) => p.name.includes("custom")).ms} ms (first)`
  );

  const seg = await timed("queue segmentation (k=3..6)", () =>
    call("POST", "/api/v1/segmentation/runs", { dataset_version_id: verId, k_candidates: [3, 4, 5, 6] })
  );
  await timed("run segmentation", () =>
    pollUntil(
      () => call("GET", `/api/v1/segmentation/runs/${seg.run.id}`),
      (d) => d.run.status !== "queued" && d.run.status !== "running",
      900000
    )
  );

  const exportJob = await timed("queue churn_scores export", () =>
    call("POST", "/api/v1/exports", { kind: "churn_scores", run_id: train.run.id })
  );
  const exportJobId = exportJob.job?.id ?? exportJob.job_id;
  await timed("generate export csv", () =>
    pollUntil(
      () => call("GET", `/api/v1/jobs/${exportJobId}`),
      (j) => j.status !== "queued" && j.status !== "running",
      600000
    )
  );
  const dl = await timed("download export csv", () =>
    (async () => {
      const res = await call("GET", `/api/v1/exports/${exportJobId}/download`, undefined, { raw: true });
      return { status: res.status, bytes: res.text.length };
    })()
  );
  console.log(`     export: ${dl.status}, ${(dl.bytes / 1e6).toFixed(2)} MB`);

  // delete dataset (code path exercised end to end; TEST-16 cascade)
  const del = await call("DELETE", `/api/v1/datasets/${dsId}`, undefined, { raw: true });
  console.log(`  delete dataset               ${del.status}`);

  console.log("\n== phase timings ==");
  const width = Math.max(...phases.map((p) => p.name.length));
  let total = 0;
  for (const p of phases) {
    const bar = "#".repeat(Math.max(1, Math.round(p.ms / 500)));
    console.log(`  ${p.name.padEnd(width)} ${p.ms.toLocaleString().padStart(9)} ms  ${bar}`);
    // probes are per-iteration; don't add to wall total
    if (!p.name.startsWith("probe")) total += p.ms;
  }
  console.log(`  ${"(total, excl. polling + probes)".padEnd(width)} ${Math.round(total).toLocaleString().padStart(9)} ms`);
  console.log(`\nLoad test complete: OK`);
}

main().catch((err) => {
  console.error("FATAL:", err.message);
  process.exit(1);
});
