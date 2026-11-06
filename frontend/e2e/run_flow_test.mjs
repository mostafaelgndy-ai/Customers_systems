// Frontend UI-flow replay (TEST-12): performs every call the Next.js UI
// makes, in the UI's order, and asserts the exact response fields the
// components consume. Any shape mismatch fails the run (exit 1).
// Usage: node frontend/e2e/run_flow_test.mjs   (BASE overrides API origin;
// backend must run with INLINE_JOBS=true so jobs execute synchronously.)

const BASE = process.env.BASE ?? "http://localhost:8000";

const issues = [];
function flag(area, message) {
  issues.push({ area, message });
  console.log(`  [FLAG] (${area}) ${message}`);
}
function ok(area, message) {
  console.log(`  [ok] (${area}) ${message}`);
}

function assertHas(area, obj, path, context = "") {
  const parts = path.split(".");
  let cur = obj;
  for (const part of parts) {
    if (cur === null || cur === undefined || !(part in cur)) {
      flag(area, `missing field '${path}' ${context}`);
      return undefined;
    }
    cur = cur[part];
  }
  return cur;
}

let TOKEN = "";
let ORG = "";

async function call(method, path, body, { form, headers = {}, raw = false } = {}) {
  const allHeaders = { ...headers };
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    allHeaders["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(`${BASE}${path}`, { method, headers: allHeaders, body: payload });
  const authHeaders = {
    Authorization: `Bearer ${TOKEN}`,
    "X-Organization-ID": ORG,
  };
  if (raw) {
    return res;
  }
  const text = await res.text();
  let json;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    if (!res.ok) {
      throw new Error(`${method} ${path} -> ${res.status} (non-JSON): ${text.slice(0, 120)}`);
    }
    return text;
  }
  if (!res.ok) {
    throw new Error(`${method} ${path} -> ${res.status}: ${JSON.stringify(json).slice(0, 200)}`);
  }
  return json;
}

async function authed(method, path, body, opts = {}) {
  const { headers = {}, ...rest } = opts;
  return call(method, path, body, { ...rest, headers: { ...authedHeaders(), ...headers } });
}

function authedHeaders() {
  return { Authorization: `Bearer ${TOKEN}`, "X-Organization-ID": ORG };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function pollJob(area, jobId, { timeoutMs = 150000 } = {}) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const job = await authed("GET", `/api/v1/jobs/${jobId}`);
    if (job.status === "succeeded") return job;
    if (job.status === "failed" || job.status === "cancelled") {
      flag(area, `job ${jobId.slice(0, 8)} ended: ${job.status} ${JSON.stringify(job.error)}`);
      return job;
    }
    await sleep(1500);
  }
  flag(area, `job ${jobId.slice(0, 8)} timed out`);
  return null;
}

// ---------------------------------------------------------------------------

// Synthetic but realistically-sized dataset (small fixtures fail training).
function buildSyntheticData() {
  const customers = [];
  const orders = [];
  const startDate = new Date(Date.UTC(2024, 0, 1));
  const rand = mulberry32(42);

  for (let i = 0; i < 150; i++) {
    const id = `C${String(i + 1).padStart(4, "0")}`;
    const created = new Date(startDate.getTime() + Math.floor(rand() * 60) * 86400000);
    customers.push(
      [
        id,
        created.toISOString().replace("Z", ""),
        ["US", "DE", "UK", "FR"][i % 4],
        ["google", "facebook", "organic", "email"][i % 4],
        i % 12 === 0 ? "inactive" : "active",
      ].join(",")
    );
    const nOrders = 1 + Math.floor(rand() * 12);
    for (let o = 0; o < nOrders; o++) {
      const orderDate = new Date(created.getTime() + Math.floor(rand() * 420) * 86400000);
      if (orderDate > new Date(Date.UTC(2025, 2, 20))) continue;
      orders.push(
        [
          `O${String(orders.length + 50001).padStart(6, "0")}`,
          id,
          `${orderDate.toISOString().slice(0, 10)} ${orderDate.toISOString().slice(11, 19)}`,
          rand() < 0.85 ? "completed" : rand() < 0.5 ? "refunded" : "returned",
          (10 + Math.floor(rand() * 490)).toFixed(2),
          "USD",
          rand() < 0.3 ? "5.00" : "0.00",
          "10.00",
          String(1 + Math.floor(rand() * 3)),
        ].join(",")
      );
    }
  }
  return {
    customersCsv:
      "customer_id,created_at,country,acquisition_channel,customer_status\n" + customers.join("\n"),
    ordersCsv:
      "order_id,customer_id,order_date,order_status,order_amount,currency,discount_amount,shipping_amount,item_count\n" +
      orders.join("\n"),
  };
}

function mulberry32(seed) {
  let a = seed;
  return function () {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------------------------------------------------------------------------

async function main() {
  console.log("== 1. login page flow (register + me) ==");
  const email = `uitest${Date.now()}@test.io`;
  const reg = await call("POST", "/api/v1/auth/register", {
    email,
    password: "Tester1234!",
    full_name: "UI Tester",
    organization_name: "UI Test Org",
  });
  assertHas("UI-02", reg, "access_token");
  assertHas("UI-02", reg, "refresh_token");
  TOKEN = reg.access_token;

  const meRes = await authed("GET", "/api/v1/auth/me");
  assertHas("UI-02", meRes, "user.email");
  const org = meRes.organizations?.[0];
  if (!org) flag("UI-02", "/auth/me organizations empty; org picker has nothing to select");
  else {
    assertHas("UI-02", org, "id", "(organizations[0])");
    assertHas("UI-02", org, "name", "(organizations[0])");
    assertHas("UI-02", org, "role", "(organizations[0])");
    ORG = org.id;
  }
  ok("UI-02", "register + me shapes");

  // UI-08 overview calls (page.tsx) — fresh account: all should be empty-safe
  console.log("== 2. overview empty-state calls ==");
  const emptyDatasets = await authed("GET", "/api/v1/datasets");
  assertHas("UI-08", emptyDatasets, "total");
  const emptyRuns = await authed("GET", "/api/v1/churn/model-runs?page=1&page_size=50");
  assertHas("UI-08", emptyRuns, "total");
  if (!("items" in emptyRuns)) flag("UI-08", "churn/model-runs list missing 'items'");
  const emptySeg = await authed("GET", "/api/v1/segmentation/runs?page=1&page_size=50");
  assertHas("UI-08", emptySeg, "total");
  if (!("items" in emptySeg)) flag("UI-08", "segmentation/runs list missing 'items'");
  ok("UI-08", "overview resources exist and are total/items shaped");

  console.log("== 3. datasets list + create (datasets page) ==");
  const dsList = await authed("GET", "/api/v1/datasets?page=1&page_size=50");
  assertHas("datasets-page", dsList, "items");
  const created = await authed("POST", "/api/v1/datasets", { name: "UI Test Dataset" });
  assertHas("datasets-page", created, "dataset.id");
  const dsFields = ["name", "created_at"];
  for (const f of dsFields) {
    const got = assertHas("datasets-page", created.dataset, f, "(created dataset row)");
    if (got === undefined && dsList.items?.length) {
      // also check list-item shape
      flag("datasets-page", `dataset row missing '${f}' (needed for list table)`);
    }
  }
  const dsId = created.dataset.id;

  console.log("== 4. templates (unauthenticated plain links) ==");
  for (const kind of ["customers", "orders"]) {
    const res = await call("GET", `/api/v1/datasets/templates/${kind}`, undefined, { raw: true });
    const text = await res.text();
    if (!res.ok) flag("UI-03", `template ${kind} -> ${res.status}`);
    else if (!text.includes("customer_id")) flag("UI-03", `template ${kind} missing customer_id header`);
  }
  ok("UI-03", "templates reachable without auth (plain <a href>)");

  console.log("== 5. upload flow (datasets/[id] page) ==");
  const fileIds = {};
  const filesToUpload = buildSyntheticData();
  const uploads = {
    customers: { csv: filesToUpload.customersCsv, name: "ui_customers.csv" },
    orders: { csv: filesToUpload.ordersCsv, name: "ui_orders.csv" },
  };
  for (const kind of ["customers", "orders"]) {
    const form = new FormData();
    form.append("kind", kind);
    form.append("file", new File([uploads[kind].csv], uploads[kind].name, { type: "text/csv" }));
    const up = await authed("POST", `/api/v1/datasets/${dsId}/files`, undefined, { form });
    assertHas("UI-04", up, "file.id");
    for (const f of ["original_filename", "size_bytes", "mapping", "created_at"]) {
      assertHas("UI-04", up.file, f, "(uploaded file row)");
    }
    if (!Array.isArray(up.file.headers)) {
      flag("UI-04", "file.headers not an array; mapping editor select cannot render");
    }
    assertHas("UI-04", up, "suggested_mapping", "(suggested mapping preselect)");
    fileIds[kind] = up.file.id;
    // PATCH the suggested mapping exactly as the mapping editor does
    const patch = await authed("PATCH", `/api/v1/datasets/${dsId}/files/${up.file.id}`, {
      mapping: up.suggested_mapping,
    });
    assertHas("UI-04", patch, "ready_for_version", "(mapping save)");
    if (patch.ready_for_version !== true) {
      flag("UI-04", `suggested mapping for ${kind} is not version-ready (ready_for_version=false)`);
    }
  }

  console.log("== 6. dataset detail reload ==");
  const detail = await authed("GET", `/api/v1/datasets/${dsId}`);
  assertHas("datasets-detail", detail, "name");
  if (!Array.isArray(detail.files)) flag("datasets-detail", "detail.files not an array");
  if (!Array.isArray(detail.versions)) flag("datasets-detail", "detail.versions not an array");
  ok("datasets-detail", "files/versions present");

  console.log("== 7. version create + validation report ==");
  const ver = await authed("POST", `/api/v1/datasets/${dsId}/versions`, {
    customers_file_id: fileIds.customers,
    orders_file_id: fileIds.orders,
  });
  assertHas("UI-05", ver, "version.id");
  assertHas("UI-05", ver, "job_id");
  const verId = ver.version.id;
  let verDetail = null;
  const start = Date.now();
  let sawProgressField = false;
  while (Date.now() - start < 120000) {
    verDetail = await authed("GET", `/api/v1/datasets/${dsId}/versions/${verId}`);
    if (typeof verDetail.job_id !== "string") flag("UI-05", "version detail missing job_id string");
    if (verDetail.status === "validating") {
      sawProgressField = sawProgressField || typeof verDetail.progress === "number";
    } else break;
    await sleep(1500);
  }
  assertHas("UI-05", verDetail, "status", "(after validation)");
  assertHas("UI-05", verDetail, "error_summary");
  assertHas("UI-05", verDetail, "report.summary");
  assertHas("UI-05", verDetail, "report.issue_counts");
  ok(`UI-05 version status = ${verDetail.status}`);
  if (verDetail.status !== "valid") {
    flag(
      "UI-05",
      `template-based version ended '${verDetail.status}' (UI valid-state flow untestable) — error_summary: ${JSON.stringify(verDetail.error_summary)?.slice(0, 120)}`
    );
  }
  const summaryKinds = Object.keys(verDetail.report?.summary ?? {});
  let nestedSummaryValues = 0;
  for (const k of summaryKinds) {
    for (const [metricKey, metricVal] of Object.entries(verDetail.report.summary[k])) {
      if (metricVal !== null && typeof metricVal === "object") {
        // Nested dicts (status_counts, currency_counts…) are the API contract;
        // the UI renders them via summaryValue() as "key: v · key2: v2".
        nestedSummaryValues += 1;
      }
    }
  }
  if (nestedSummaryValues > 0) {
    ok(`UI-05 ${nestedSummaryValues} nested summary values present (UI renders them flattened)`);
  } else {
    ok("UI-05 summary values all flat");
  }

  console.log("== 8. churn training (UI-06 launch + overview metrics) ==");
  const train = await authed("POST", "/api/v1/churn/model-runs", {
    dataset_version_id: verId,
    observation_window_days: 90,
    label_horizon_days: 60,
    min_period_customers: 5,
  });
  assertHas("UI-06", train, "run.id");
  assertHas("UI-06", train, "run.status");
  const runId = train.run.id;
  if (train.run.job_id) {
    await pollJob("UI-06", train.run.job_id);
  }
  let runDetail = null;
  const t2 = Date.now();
  while (Date.now() - t2 < 180000) {
    runDetail = await authed("GET", `/api/v1/churn/model-runs/${runId}`);
    if (runDetail.run.status !== "queued" && runDetail.run.status !== "running") break;
    await sleep(2000);
  }
  ok(`UI-06 churn run status = ${runDetail?.run?.status}`);
  if (typeof runDetail?.run?.version_number !== "number") {
    flag("L3", "churn model-run detail missing numeric version_number (customer detail version labels)");
  }
  if (runDetail?.run?.status !== "succeeded") {
    flag("UI-06", `training ended ${runDetail?.run?.status}: ${JSON.stringify(runDetail?.run?.error)?.slice(0, 160)}`);
  } else {
    const m = runDetail.run.metrics ?? {};
    if (!("selection" in m)) flag("UI-08", "overview validation table reads metrics.selection.selected — missing on run");
    else {
      if (!("selected_model" in m.selection)) flag("UI-08", "metrics.selection.selected_model missing");
      else ok(`UI-08 selected model = ${m.selection.selected_model}`);
    }
    const validation = assertHas("UI-08", m, "validation", "(overview metrics table)");
    if (validation && typeof validation === "object") {
      for (const [model, vals] of Object.entries(validation)) {
        if (typeof vals !== "object" || vals === null) {
          flag("UI-08", `metrics.validation.${model} not an object (table renders pr_auc/roc_auc)`);
        } else {
          if (vals.pr_auc === undefined) flag("UI-08", `metrics.validation.${model}.pr_auc missing`);
          if (vals.roc_auc === undefined) flag("UI-08", `metrics.validation.${model}.roc_auc missing`);
        }
      }
    }
    if (typeof runDetail.run.train_cutoff_date !== "string" && runDetail.run.train_cutoff_date !== null) {
      flag("UI-08/UI-09", "run.train_cutoff_date neither string nor null (used in selectors)");
    }
  }

  console.log("== 9. churn scoring + risk table + customer detail ==");
  const scoreJob = await authed("POST", "/api/v1/churn/scores", { model_run_id: runId });
  // score run response: UI does not read it; poll via job id if present
  if (scoreJob.job_id) {
    await pollJob("UI-09", scoreJob.job_id);
  } else if (scoreJob.job) {
    await pollJob("UI-09", scoreJob.job.id);
  } else {
    flag("UI-09", "POST /churn/scores response has neither job_id nor job.id — UI cannot poll");
  }
  const scores = await authed(
    "GET",
    `/api/v1/churn/scores?model_run_id=${runId}&order=desc&page=1&page_size=25`
  );
  assertHas("UI-09", scores, "items");
  assertHas("UI-09", scores, "total");
  if (!("pages" in scores)) flag("UI-09", "scores list missing 'pages' — pagination breaks (NaN)");
  const firstScore = scores.items?.[0];
  if (firstScore) {
    for (const f of ["customer_id", "churn_probability", "risk_band"]) {
      assertHas("UI-09", firstScore, f, "(risk table row)");
    }
    const bands = new Set(scores.items.map((s) => s.risk_band));
    for (const b of bands) {
      if (!["low", "medium", "high"].includes(b)) {
        flag("UI-09", `unexpected risk_band value '${b}' (UI color map only covers low/medium/high)`);
      }
    }
    const custId = firstScore.customer_id;
    const cust = await authed(
      "GET",
      `/api/v1/customers/${encodeURIComponent(custId)}?dataset_version_id=${verId}`
    );
    assertHas("UI-10", cust, "data_version");
    assertHas("UI-10", cust, "churn.risk_band");
    assertHas("UI-10", cust, "churn.churn_probability");
    assertHas("UI-10", cust, "churn.model_run_id");
    const expl = cust.churn?.explanation;
    if (!expl) flag("UI-10", "customer detail churn.explanation missing entirely");
    else {
      const tops = assertHas("UI-10", expl, "top_signals", "(top signals list)");
      if (Array.isArray(tops)) {
        if (tops.length === 0) flag("UI-10", "top_signals empty array — signals section shows empty");
        for (const sig of tops.slice(0, 3)) {
          if (!("feature" in sig)) flag("UI-10", "signal missing 'feature'");
          if (!("direction" in sig)) flag("UI-10", "signal missing 'direction'");
          else if (!["up", "down", "higher_risk", "lower_risk"].includes(sig.direction)) {
            flag("UI-10", `signal.direction '${sig.direction}' unhandled (UI arrow maps up/higher_risk and down/lower_risk)`);
          }
        }
      }
    }
    ok("UI-10", "customer detail shape");
  } else {
    flag("UI-09", "no scored customers returned — risk table would be blank");
  }

  console.log("== 10. segmentation (UI-11/12/15) ==");
  const seg = await authed("POST", "/api/v1/segmentation/runs", {
    dataset_version_id: verId,
    observation_window_days: 90,
    k_candidates: [3],
  });
  assertHas("segments-page", seg, "run.id");
  const segId = seg.run.id;
  let segDetail = null;
  const t3 = Date.now();
  while (Date.now() - t3 < 240000) {
    segDetail = await authed("GET", `/api/v1/segmentation/runs/${segId}`);
    if (segDetail.run.status !== "queued" && segDetail.run.status !== "running") break;
    if (typeof segDetail.run.job_id !== "string") {
      // runs page does not show seg run job; only note missing job handle
    }
    await sleep(2000);
  }
  ok(`segments run status = ${segDetail?.run?.status}`);
  if (segDetail?.run?.status !== "succeeded") {
    flag("UI-11", `segmentation ended ${segDetail?.run?.status}: ${JSON.stringify(segDetail?.run?.error)?.slice(0, 160)}`);
  } else {
    for (const f of ["profiles", "warnings", "selected_k", "cutoff_date"]) {
      assertHas("UI-11", segDetail.run, f, "(run detail)");
    }
    // profiles is a wrapper object: {segments: [...], labels_editable: bool}
    const wrapper = segDetail.run.profiles;
    const profileRows = wrapper?.segments ?? [];
    if (!Array.isArray(profileRows)) {
      flag("UI-12", "run.profiles.segments not an array (segments page iterates it)");
    } else if (typeof wrapper.labels_editable !== "boolean") {
      flag("UI-12", "run.profiles.labels_editable not a boolean (rename gate reads it)");
    }
    if (profileRows.length > 0) {
      const p = profileRows[0];
      for (const pf of ["cluster_id", "count", "share", "descriptive_label", "label_editable"]) {
        assertHas("UI-12", p, pf, "(profile row)");
      }
      for (const feat of ["recency_days", "frequency", "monetary_value", "avg_order_value", "tenure_days"]) {
        if (!(feat in p)) {
          flag("UI-12", `profile missing feature column '${feat}' (UI table renders it)`);
        } else if (p[feat] && !("median" in p[feat])) {
          flag("UI-12", `profile.${feat} lacks 'median' (MetricCell renders median)`);
        }
      }
      ok("UI-12 profile row shapes (wrapper {segments, labels_editable})");
    }
    // rename exactly as the segments page does
    const p0 = profileRows[0];
    if (p0) {
      const renamed = await authed(
        "POST",
        `/api/v1/segmentation/runs/${segId}/segments/${p0.cluster_id}/rename`,
        { segment_label: "UI Renamed Segment" }
      );
      if (!renamed) flag("UI-12", "rename returned empty body (UI reloads anyway)");
      const after = await authed("GET", `/api/v1/segmentation/runs/${segId}`);
      const pAfter = (after.run.profiles?.segments ?? []).find((x) => x.cluster_id === p0.cluster_id);
      if (!pAfter || pAfter.descriptive_label !== "UI Renamed Segment") {
        flag("UI-12", "rename did not persist to profiles.descriptive_label — UI rename looks broken to users");
      } else ok("UI-12 rename persisted");

      // UI-15 compare: second run on same version
      const seg2 = await authed("POST", "/api/v1/segmentation/runs", {
        dataset_version_id: verId,
        observation_window_days: 90,
        k_candidates: [3],
      });
      const seg2Detail = await waitForSegRun(seg2.run.id);
      if (seg2Detail?.run?.status !== "succeeded") {
        flag("UI-15", `second seg run ended ${seg2Detail?.run?.status}; compare view untestable`);
      } else {
        const cmp = await authed(
          "GET",
          `/api/v1/segmentation/compare?run_a_id=${segId}&run_b_id=${seg2.run.id}`
        );
        assertHas("UI-15", cmp, "segment_share_deltas");
        assertHas("UI-15", cmp, "max_share_shift");
        if (!Array.isArray(cmp.caveats)) flag("UI-15", "compare caveats not a list");
        ok("UI-15 compare shape");
      }
    }
  }

  console.log("== 11. exports (UI-13) ==");
  const exp = await authed("POST", "/api/v1/exports", {
    kind: "churn_scores",
    run_id: runId,
  });
  const expJobId = exp?.job?.id;
  if (!expJobId) flag("UI-13", "POST /exports response missing job.id");
  else {
    const job = await pollJob("UI-13", expJobId);
    if (job?.status === "succeeded") {
      const dl = await fetch(`${BASE}/api/v1/exports/${expJobId}/download`, {
        headers: authedHeaders(),
      });
      const text = await dl.text();
      if (!dl.ok) flag("UI-13", `download -> ${dl.status}`);
      else if (!dl.headers.get("content-type")?.startsWith("text/csv")) {
        flag("UI-13", `download content-type '${dl.headers.get("content-type")}' not text/csv`);
      } else if (!text.startsWith("customer_id")) {
        flag("UI-13", "download CSV missing customer_id header");
      } else ok("UI-13 download ok (CSV streamed)");
    }
  }
  // exports history list (what the page polls)
  const expJobs = await authed("GET", "/api/v1/jobs?job_type=export_csv&page=1&page_size=25");
  if (!("items" in expJobs)) flag("UI-13", "jobs?job_type=export_csv missing items");
  else {
    const jr = expJobs.items?.[0];
    if (jr) {
      for (const f of ["id", "type", "status", "progress", "created_at"]) {
        assertHas("UI-13", jr, f, "(export history row)");
      }
      if (!expJobs.total !== undefined) ok("UI-13 history fields ok");
    }
  }

  console.log("== 12. jobs page fields ==");
  const jobsList = await authed("GET", "/api/v1/jobs?page=1&page_size=25");
  if (jobsList.items?.[0]) {
    for (const f of ["id", "type", "status", "progress", "created_at"]) {
      assertHas("UI-07", jobsList.items[0], f, "(jobs table row)");
    }
    assertHas("UI-07", jobsList, "pages");
    ok("UI-07 jobs row fields");
  } else flag("UI-07", "jobs list empty though jobs ran — filter default wrong?");

  console.log("== 13. runs page type filter values ==");
  for (const jt of ["validate_dataset", "compute_features", "train_churn_model", "score_churn_model", "run_segmentation", "export_csv"]) {
    const filtered = await authed("GET", `/api/v1/jobs?job_type=${jt}&page=1&page_size=5`);
    for (const item of filtered.items ?? []) {
      if (item.type !== jt) flag("UI-07", `filter '${jt}' returned type '${item.type}'`);
    }
  }
  ok("UI-07 job_type filter options all valid");

  console.log("\n================ ISSUE SUMMARY ================");
  if (issues.length === 0) {
    console.log("No contract/UI-flow issues found.");
  } else {
    for (const [i, issue] of issues.entries()) {
      console.log(`${i + 1}. [${issue.area}] ${issue.message}`);
    }
  }
  console.log(`\nTotal flagged: ${issues.length}`);
  // CI gate (TEST-12): any contract-shape flag fails the run.
  process.exit(issues.length > 0 ? 1 : 0);
}

async function waitForSegRun(id) {
  const t = Date.now();
  while (Date.now() - t < 240000) {
    const d = await authed("GET", `/api/v1/segmentation/runs/${id}`);
    if (d.run.status !== "queued" && d.run.status !== "running") return d;
    await sleep(2000);
  }
  flag("UI-11", `seg run ${id.slice(0, 8)} polling timed out`);
  return null;
}

main().catch((err) => {
  console.error("FATAL:", err.message);
  console.error("\nPartial flags so far:", issues.length);
  process.exit(1);
});
