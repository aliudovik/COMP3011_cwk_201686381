const CURRENT_USER_ID = window.CURRENT_USER_ID;

async function apiIngest(provider) {
  const log = document.getElementById("homeLog");
  const prov = provider || "spotify";

  // Choose source based on provider
  let source = "top"; // default for spotify
  if (prov === "youtube") {
    source = "liked_videos";
  }

  if (log) log.textContent = `Ingesting ${prov} (${source})...\n`;

  const r = await fetch("/api/ingest", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ provider: prov, source: source, user_id: CURRENT_USER_ID })
  });

  const j = await r.json();
  if (log) log.textContent += JSON.stringify(j, null, 2) + "\n";
}

async function apiProfileRebuild() {
  const log = document.getElementById("homeLog");
  if (log) log.textContent += "Rebuilding profile...\n";
  const r = await fetch("/api/profile/rebuild", {
  method: "POST",
  headers: {"Content-Type":"application/json"},
  body: JSON.stringify({ user_id: CURRENT_USER_ID })
});
  const j = await r.json();
  if (log) log.textContent += JSON.stringify(j, null, 2) + "\n";
}

function getSelectedMood() {
  const el = document.querySelector("input[name='mood']:checked");
  return el ? el.value : null;
}

async function apiGenerate() {
  const log = document.getElementById("genLog");
  if (log) log.textContent = "Generating...\n";

  const mood = getSelectedMood();
  const instrumental = document.getElementById("instrumental").checked;
  const customMode = document.getElementById("customMode").checked;
  const titleHint = document.getElementById("titleHint").value;
  const styleHint = document.getElementById("styleHint").value;

  const r = await fetch("/api/generate", {
    user_id: CURRENT_USER_ID,
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      mood,
      instrumental,
      custom_mode: customMode,
      title_hint: titleHint,
      style_hint: styleHint
    })
  });
  const j = await r.json();
  if (log) log.textContent += JSON.stringify(j, null, 2) + "\n";

  if (j.generation_id) {
    window.location.href = "/dev/generation/" + j.generation_id;
  }
}

async function pollGeneration(genId) {
  const r = await fetch("/api/generation/" + genId);
  const j = await r.json();

  const statusEl = document.getElementById("status");
  if (statusEl) statusEl.textContent = j.status;

  const resultEl = document.getElementById("result");
  if (resultEl) resultEl.textContent = JSON.stringify(j.result, null, 2);

  if (j.error) {
    alert("Generation error: " + j.error);
  }
}
