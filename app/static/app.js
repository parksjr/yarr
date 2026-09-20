const $ = (id) => document.getElementById(id);

const fetchForm = $("fetch-form");
const fetchButton = $("fetch-button");
const fetchError = $("fetch-error");
const progress = $("progress");
const progressFill = $("progress-fill");
const progressText = $("progress-text");
const resultCard = $("result-card");
const chaptersCard = $("chapters-card");
const chaptersText = $("chapters-text");
const singleModeButton = $("single-mode-button");
const splitModeButton = $("split-mode-button");
const wizardBar = $("wizard-bar");
const prevTrack = $("prev-track");
const nextTrack = $("next-track");
const wizardPosition = $("wizard-position");
const wizardChapter = $("wizard-chapter");
const backSingle = $("back-single");
const formHeading = $("form-heading");
const saveForm = $("save-form");
const saveButton = $("save-button");
const resetButton = $("reset-button");
const saveError = $("save-error");
const saveSuccess = $("save-success");
const coverImg = $("cover-img");
const coverFallback = $("cover-fallback");
const pathPreview = $("path-preview");
const artistList = $("artist-list");
const albumList = $("album-list");

let jobId = null;
let currentJob = null;
let pollTimer = null;
let previewTimer = null;
let mode = "single";
let tracks = [];
let trackIndex = 0;
let chapters = [];

// Shared fields that can be synced across all tracks in split mode.
const sharedFieldMap = {
  "f-artist": { applyId: "apply-artist", key: "artist" },
  "f-album": { applyId: "apply-album", key: "album" },
  "f-album-artist": { applyId: "apply-album-artist", key: "album_artist" },
  "f-date": { applyId: "apply-date", key: "date" },
  "f-genre": { applyId: "apply-genre", key: "genre" }
};

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      if (data && data.detail) message = data.detail;
    } catch (_) {
      // keep the default message
    }
    throw new Error(message);
  }
  return res.json();
}

function setError(el, message) {
  if (!message) {
    el.textContent = "";
    el.classList.add("hidden");
    return;
  }
  el.textContent = message;
  el.classList.remove("hidden");
}

function showProgress(show) {
  progress.classList.toggle("hidden", !show);
}

function setProgress(value, text) {
  const pct = Math.max(0, Math.min(1, value || 0)) * 100;
  progressFill.style.width = `${pct}%`;
  progressText.textContent = text || "Working…";
}

function formatTime(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return "";
  const s = Math.max(0, Math.round(Number(seconds)));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

async function loadArtists() {
  try {
    const data = await api("/api/library");
    artistList.innerHTML = "";
    for (const artist of data.artists || []) {
      const opt = document.createElement("option");
      opt.value = artist;
      artistList.appendChild(opt);
    }
  } catch (_) {
    // The library may not be mounted yet; that is fine.
  }
}

async function loadAlbums(artist) {
  albumList.innerHTML = "";
  if (!artist) return;
  try {
    const data = await api(`/api/library/albums?artist=${encodeURIComponent(artist)}`);
    for (const album of data.albums || []) {
      const opt = document.createElement("option");
      opt.value = album;
      albumList.appendChild(opt);
    }
  } catch (_) {
    // ignore listing errors
  }
}

function currentMeta() {
  const trackRaw = $("f-track").value.trim();
  let track = null;
  if (trackRaw !== "") {
    const n = parseInt(trackRaw, 10);
    if (!Number.isNaN(n) && n > 0) track = n;
  }
  return {
    title: $("f-title").value.trim(),
    artist: $("f-artist").value.trim(),
    album_artist: $("f-album-artist").value.trim(),
    album: $("f-album").value.trim(),
    date: $("f-date").value.trim(),
    genre: $("f-genre").value.trim(),
    track
  };
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(updatePreview, 350);
}

async function updatePreview() {
  if (!jobId) return;
  pathPreview.textContent = "…";
  try {
    const data = await api(`/api/jobs/${jobId}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentMeta())
    });
    pathPreview.textContent = data.path;
  } catch (err) {
    pathPreview.textContent = err.message;
  }
}

function fillForm(meta) {
  $("f-artist").value = meta.artist || "";
  $("f-title").value = meta.title || "";
  $("f-album").value = meta.album || "";
  $("f-album-artist").value = meta.album_artist || "";
  $("f-track").value = meta.track ? String(meta.track) : "";
  $("f-date").value = meta.date || "";
  $("f-genre").value = meta.genre || "";
}

function setCover(job) {
  coverImg.onerror = () => {
    coverImg.classList.add("hidden");
    coverFallback.classList.remove("hidden");
  };
  coverImg.onload = () => {
    coverImg.classList.remove("hidden");
    coverFallback.classList.add("hidden");
  };
  if (!job.has_cover) {
    coverImg.classList.add("hidden");
    coverFallback.classList.remove("hidden");
    return;
  }
  coverImg.src = `/api/jobs/${job.id}/cover?t=${Date.now()}`;
}

function showChaptersCard(show) {
  chaptersCard.classList.toggle("hidden", !show);
}

function showWizard(show) {
  wizardBar.classList.toggle("hidden", !show);
}

function commitCurrentTrack() {
  if (mode === "split" && tracks[trackIndex]) {
    tracks[trackIndex] = currentMeta();
  }
}

function resetApplyToggles() {
  for (const inputId in sharedFieldMap) {
    $(sharedFieldMap[inputId].applyId).checked = false;
  }
}

function syncSharedField(inputId) {
  const cfg = sharedFieldMap[inputId];
  if (!cfg || mode !== "split" || !tracks.length) return;
  if (!$(cfg.applyId).checked) return;
  const value = $(inputId).value.trim();
  for (let i = 0; i < tracks.length; i += 1) {
    tracks[i][cfg.key] = value;
  }
}

function renderWizardPosition() {
  wizardPosition.textContent = `${trackIndex + 1} of ${tracks.length}`;
}

function renderWizardChapter() {
  const ch = chapters[trackIndex];
  const tr = tracks[trackIndex] || {};
  if (!ch) {
    wizardChapter.textContent = tr.title || "";
    return;
  }
  const start = formatTime(ch.start);
  const end = ch.end != null ? formatTime(ch.end) : "end";
  wizardChapter.textContent = `Chapter: “${ch.title}” (${start} – ${end})`;
}

function showTrack() {
  fillForm(tracks[trackIndex] || {});
  renderWizardPosition();
  renderWizardChapter();
  updatePreview();
}

function enterSingleMode() {
  mode = "single";
  saveForm.classList.remove("split-mode");
  resetApplyToggles();
  showWizard(false);
  showChaptersCard(!!(currentJob && currentJob.has_chapters));
  formHeading.textContent = "Check the metadata";
  saveButton.textContent = "Save to library";
  if (currentJob) fillForm(currentJob.metadata || {});
  updatePreview();
}

async function enterSplitMode() {
  if (mode === "split") return;
  if (!jobId) return;
  setError(saveError, "");
  splitModeButton.disabled = true;
  try {
    const data = await api(`/api/jobs/${jobId}/split`, {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    });
    tracks = data.tracks || [];
    if (!tracks.length) throw new Error("No chapter tracks were returned.");
    mode = "split";
    trackIndex = 0;
    saveForm.classList.add("split-mode");
    resetApplyToggles();
    showChaptersCard(false);
    showWizard(true);
    formHeading.textContent = `Check each track (${tracks.length} songs)`;
    saveButton.textContent = `Save ${tracks.length} tracks to library`;
    showTrack();
  } catch (err) {
    setError(saveError, err.message);
  } finally {
    splitModeButton.disabled = false;
  }
}

function renderJob(job) {
  if (job.status === "running" || job.status === "queued") {
    showProgress(true);
    setProgress(job.progress, job.stage);
  }
  if (job.status === "error") {
    showProgress(false);
    fetchButton.disabled = false;
    setError(fetchError, job.error || "Something went wrong.");
  }
  if (job.status === "done") {
    showProgress(false);
    fetchButton.disabled = false;
    jobId = job.id;
    currentJob = job;
    chapters = job.chapters || [];
    tracks = [];
    mode = "single";
    trackIndex = 0;
    saveForm.classList.remove("split-mode");
    resetApplyToggles();
    fillForm(job.metadata || {});
    setCover(job);
    resultCard.classList.remove("hidden");
    setError(saveError, "");
    saveSuccess.classList.add("hidden");
    saveSuccess.textContent = "";
    showWizard(false);
    formHeading.textContent = "Check the metadata";
    saveButton.textContent = "Save to library";
    if (job.has_chapters) {
      chaptersText.textContent = `This video has ${chapters.length} chapters. Save it as one track, or split it into ${chapters.length} songs and edit each one before saving.`;
      splitModeButton.textContent = `Split into ${chapters.length} songs`;
      showChaptersCard(true);
    } else {
      showChaptersCard(false);
    }
    loadArtists();
    loadAlbums(($("f-artist").value || "").trim());
    updatePreview();
  }
}

function startPolling() {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (!jobId) return;
    try {
      const job = await api(`/api/jobs/${jobId}`);
      renderJob(job);
      if (job.status === "done" || job.status === "error") {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    } catch (err) {
      clearInterval(pollTimer);
      pollTimer = null;
      showProgress(false);
      fetchButton.disabled = false;
      setError(fetchError, err.message);
    }
  }, 1500);
}

fetchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = $("url-input").value.trim();
  if (!url) return;
  setError(fetchError, "");
  setError(saveError, "");
  saveSuccess.classList.add("hidden");
  saveSuccess.textContent = "";
  resultCard.classList.add("hidden");
  fetchButton.disabled = true;
  showProgress(true);
  setProgress(0, "Queued…");
  try {
    const data = await api("/api/fetch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });
    jobId = data.job_id;
    startPolling();
  } catch (err) {
    showProgress(false);
    fetchButton.disabled = false;
    setError(fetchError, err.message);
  }
});

saveForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!jobId) return;
  saveButton.disabled = true;
  setError(saveError, "");
  saveSuccess.classList.add("hidden");
  saveSuccess.textContent = "";
  try {
    if (mode === "split") {
      commitCurrentTrack();
      const data = await api(`/api/jobs/${jobId}/save_many`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tracks })
      });
      const saved = data.tracks || [];
      const dir = saved.length ? saved[0].relative.split("/").slice(0, -1).join("/") : "";
      saveSuccess.textContent = `Saved ${saved.length} tracks${dir ? ` to ${dir}/` : ""}.`;
      saveSuccess.classList.remove("hidden");
      pathPreview.textContent = `${saved.length} tracks saved`;
    } else {
      const data = await api(`/api/jobs/${jobId}/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentMeta())
      });
      saveSuccess.textContent = `Saved to ${data.relative}`;
      saveSuccess.classList.remove("hidden");
      pathPreview.textContent = data.relative;
    }
  } catch (err) {
    setError(saveError, err.message);
  } finally {
    saveButton.disabled = false;
  }
});

resetButton.addEventListener("click", () => {
  window.location.reload();
});

singleModeButton.addEventListener("click", enterSingleMode);
splitModeButton.addEventListener("click", enterSplitMode);
backSingle.addEventListener("click", enterSingleMode);

prevTrack.addEventListener("click", () => {
  commitCurrentTrack();
  if (trackIndex > 0) {
    trackIndex -= 1;
    showTrack();
  }
});

nextTrack.addEventListener("click", () => {
  commitCurrentTrack();
  if (trackIndex < tracks.length - 1) {
    trackIndex += 1;
    showTrack();
  }
});

// Per-field "apply to all tracks": checking a toggle copies the current value
// to every track, and while it stays checked, edits to that field keep all
// tracks in sync. The user can opt in per field or leave them untouched.
for (const inputId in sharedFieldMap) {
  const cfg = sharedFieldMap[inputId];
  $(cfg.applyId).addEventListener("change", () => syncSharedField(inputId));
  $(inputId).addEventListener("input", () => syncSharedField(inputId));
}

$("f-artist").addEventListener("input", () => {
  loadAlbums($("f-artist").value.trim());
  schedulePreview();
});
$("f-artist").addEventListener("change", schedulePreview);

["f-title", "f-album", "f-album-artist", "f-track", "f-date", "f-genre"].forEach((id) => {
  $(id).addEventListener("input", schedulePreview);
  $(id).addEventListener("change", schedulePreview);
});
