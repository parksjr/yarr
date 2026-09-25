const $ = (id) => document.getElementById(id);

const fetchForm = $("fetch-form");
const fetchHeading = $("fetch-heading");
const fetchButton = $("fetch-button");
const fetchError = $("fetch-error");
const fetchSuccess = $("fetch-success");
const progress = $("progress");
const progressFill = $("progress-fill");
const progressText = $("progress-text");
const cancelButton = $("cancel-button");
const resultCard = $("result-card");
const sourceBadge = $("source-badge");
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
const addToBatchButton = $("add-to-batch-button");
const resetButton = $("reset-button");
const saveError = $("save-error");
const saveSuccess = $("save-success");
const coverImg = $("cover-img");
const coverFallback = $("cover-fallback");
const pathPreview = $("path-preview");
const artistList = $("artist-list");
const albumList = $("album-list");
const batchCard = $("batch-card");
const batchCount = $("batch-count");
const batchList = $("batch-list");
const batchSaveButton = $("batch-save-button");
const batchClearButton = $("batch-clear-button");
const batchMessage = $("batch-message");
const savedCard = $("saved-card");
const savedHeading = $("saved-heading");
const savedList = $("saved-list");

let jobId = null;
let currentJob = null;
let pollTimer = null;
let previewTimer = null;
let cancelling = false;
let mode = "single";
let tracks = [];
let trackIndex = 0;
let chapters = [];

// Batch state. Every fetched single song is a queued track. `editingBatch` is
// the index of the queued track currently shown in the form, or null while the
// form shows the just-fetched (not yet queued) song.
let batchTracks = [];
let batchActive = false;
let editingBatch = null;
let batchDefaults = { artist: null, album: null, album_artist: null, date: null, genre: null };

// Shared fields that can be synced across tracks (split mode) or queued songs
// (batch mode).
const sharedFieldMap = {
  "f-artist": { applyId: "apply-artist", key: "artist" },
  "f-album": { applyId: "apply-album", key: "album" },
  "f-album-artist": { applyId: "apply-album-artist", key: "album_artist" },
  "f-date": { applyId: "apply-date", key: "date" },
  "f-genre": { applyId: "apply-genre", key: "genre" }
};

function emptyBatchDefaults() {
  return { artist: null, album: null, album_artist: null, date: null, genre: null };
}

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

function setFetchSuccess(message) {
  if (!message) {
    fetchSuccess.textContent = "";
    fetchSuccess.classList.add("hidden");
    return;
  }
  fetchSuccess.textContent = message;
  fetchSuccess.classList.remove("hidden");
}

function showProgress(show) {
  progress.classList.toggle("hidden", !show);
}

function setProgress(value, text) {
  const pct = Math.max(0, Math.min(1, value || 0)) * 100;
  progressFill.style.width = `${pct}%`;
  progressText.textContent = text || "Working…";
}

function setCancelling(on) {
  cancelling = on;
  cancelButton.disabled = on;
  cancelButton.textContent = on ? "Cancelling…" : "Cancel";
}

// The success card. The old one-line message lived in the save card, which the
// batch flow hides right after a save; this card is cleared whenever a new job
// appears, so a finished save stays readable.
function setSavedCard(items) {
  if (!items || !items.length) {
    savedCard.classList.add("hidden");
    savedList.innerHTML = "";
    savedHeading.textContent = "Saved";
    return;
  }
  const folders = [...new Set(items.map((i) => i.folder).filter(Boolean))];
  const artists = [...new Set(items.map((i) => i.artist).filter(Boolean))];
  const noun = items.length === 1 ? "song" : "songs";
  if (folders.length === 1) {
    savedHeading.textContent = `Saved ${items.length} ${noun} to ${folders[0]}/`;
  } else if (folders.length > 1) {
    savedHeading.textContent = `Saved ${items.length} ${noun} to ${folders.length} folders`;
  } else {
    savedHeading.textContent = `Saved ${items.length} ${noun}`;
  }
  savedList.innerHTML = "";
  if (folders.length <= 1) {
    savedCard.classList.remove("hidden");
    return;
  }
  // More than one destination: group the songs by folder so it is obvious that
  // each song went to its own artist/album folder.
  for (const folder of folders) {
    const inFolder = items.filter((i) => i.folder === folder);
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "saved-folder";
    name.textContent = `${folder}/`;
    li.appendChild(name);
    const count = document.createElement("span");
    count.className = "saved-count";
    count.textContent = ` — ${inFolder.length} song${inFolder.length === 1 ? "" : "s"}`;
    li.appendChild(count);
    savedList.appendChild(li);
    if (artists.length > 1) {
      for (const item of inFolder) {
        const sub = document.createElement("li");
        sub.className = "saved-count";
        sub.textContent = `• ${item.title || "(untitled)"}`;
        savedList.appendChild(sub);
      }
    }
  }
  savedCard.classList.remove("hidden");
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

function currentPreviewJobId() {
  if (mode === "split") return jobId;
  if (editingBatch !== null && batchTracks[editingBatch]) return batchTracks[editingBatch].job_id;
  return jobId;
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(updatePreview, 350);
}

async function updatePreview() {
  const previewJobId = currentPreviewJobId();
  if (!previewJobId) {
    pathPreview.textContent = "—";
    return;
  }
  pathPreview.textContent = "…";
  try {
    const data = await api(`/api/jobs/${previewJobId}/preview`, {
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

function setCoverSrc(jobId) {
  coverImg.onerror = () => {
    coverImg.classList.add("hidden");
    coverFallback.classList.remove("hidden");
  };
  coverImg.onload = () => {
    coverImg.classList.remove("hidden");
    coverFallback.classList.add("hidden");
  };
  coverImg.src = `/api/jobs/${jobId}/cover?t=${Date.now()}`;
}

function setCover(job) {
  if (!job.has_cover) {
    coverImg.classList.add("hidden");
    coverFallback.classList.remove("hidden");
    return;
  }
  setCoverSrc(job.id);
}

function setSourceBadge(source) {
  if (source === "spotify" || source === "youtube") {
    sourceBadge.textContent = source === "spotify" ? "Spotify" : "YouTube";
    sourceBadge.classList.remove("youtube", "spotify");
    sourceBadge.classList.add(source);
    sourceBadge.classList.remove("hidden");
  } else {
    sourceBadge.textContent = "";
    sourceBadge.classList.remove("youtube", "spotify");
    sourceBadge.classList.add("hidden");
  }
}

function showChaptersCard(show) {
  chaptersCard.classList.toggle("hidden", !show);
}

function showWizard(show) {
  wizardBar.classList.toggle("hidden", !show);
}

function commitCurrentTrack() {
  if ((mode === "split" || mode === "tracks") && tracks[trackIndex]) {
    tracks[trackIndex] = currentMeta();
  }
}

function commitFormToTarget() {
  if (mode !== "single") return;
  const meta = currentMeta();
  if (editingBatch !== null && batchTracks[editingBatch]) {
    batchTracks[editingBatch].meta = meta;
  } else if (currentJob) {
    currentJob.metadata = meta;
  }
}

function resetApplyToggles() {
  for (const inputId in sharedFieldMap) {
    $(sharedFieldMap[inputId].applyId).checked = false;
  }
}

function syncSharedField(inputId) {
  const cfg = sharedFieldMap[inputId];
  if (!cfg || !$(cfg.applyId).checked) return;
  const value = $(inputId).value.trim();
  if (mode === "split" || mode === "tracks") {
    for (let i = 0; i < tracks.length; i += 1) {
      tracks[i][cfg.key] = value;
    }
  } else {
    // Batch flow: apply to every queued song and the current (not yet queued)
    // song, and remember the value for songs added later.
    batchDefaults[cfg.key] = value;
    for (const b of batchTracks) b.meta[cfg.key] = value;
    if (currentJob) currentJob.metadata[cfg.key] = value;
  }
}

function applyBatchDefaults() {
  for (const inputId in sharedFieldMap) {
    const cfg = sharedFieldMap[inputId];
    if ($(cfg.applyId).checked && batchDefaults[cfg.key] !== null) {
      $(inputId).value = batchDefaults[cfg.key];
    }
  }
}

function currentSongReady() {
  return mode === "single" && currentJob && currentJob.status === "done";
}

function setBatchMessage(message, kind) {
  batchMessage.textContent = message || "";
  batchMessage.classList.toggle("hidden", !message);
  batchMessage.classList.toggle("error", kind === "error");
  batchMessage.classList.toggle("success", kind === "success");
}

// Reset the workspace after a successful save: clear the result card, the URL
// input, and any error/success text so the next fetch starts clean. The batch
// queue is intentionally left alone here; callers that save the queue clear it
// themselves first.
function resetAfterSave() {
  jobId = null;
  currentJob = null;
  chapters = [];
  tracks = [];
  trackIndex = 0;
  mode = "single";
  editingBatch = null;
  resetApplyToggles();
  $("url-input").value = "";
  resultCard.classList.add("hidden");
  showWizard(false);
  showChaptersCard(false);
  setSourceBadge(null);
  setError(saveError, "");
  setError(fetchError, "");
  saveSuccess.classList.add("hidden");
  saveSuccess.textContent = "";
  pathPreview.textContent = "—";
  clearTimeout(previewTimer);
  showProgress(false);
  setFetchSuccess("");
  updateModeClasses();
  renderBatchPanel();
  $("url-input").focus();
}

// Abort an in-flight fetch. The server stops the downloader (and any
// subprocess it spawned) and deletes the staging directory, so the result is a
// clean break: no progress bar, no result card, nothing queued.
async function cancelFetch() {
  if (!jobId || cancelling) return;
  const cancelledId = jobId;
  setCancelling(true);
  try {
    await api(`/api/jobs/${cancelledId}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "Cancelled by you." })
    });
    clearInterval(pollTimer);
    pollTimer = null;
    // Only reset if this job is still the one on screen.
    if (jobId === cancelledId) {
      jobId = null;
      currentJob = null;
      clearTimeout(previewTimer);
      resultCard.classList.add("hidden");
      showProgress(false);
      setProgress(0, "");
      setError(fetchError, "");
      setFetchSuccess("Cancelled. Nothing was saved.");
    }
  } catch (err) {
    clearInterval(pollTimer);
    pollTimer = null;
    if (jobId === cancelledId) showProgress(false);
    setError(fetchError, err.message);
  } finally {
    setCancelling(false);
    fetchButton.disabled = false;
    $("url-input").focus();
  }
}

function updateModeClasses() {
  const inSplit = mode === "split" || mode === "tracks";
  const editingTrack = !inSplit && editingBatch !== null;
  addToBatchButton.classList.toggle("hidden", inSplit || editingTrack);
  saveButton.classList.toggle("hidden", editingTrack);
  backSingle.classList.toggle("hidden", mode === "tracks");
  if (inSplit) {
    saveButton.textContent = `Save ${tracks.length} tracks to library`;
  } else {
    saveButton.textContent = batchActive ? "Save this song" : "Save to library";
    formHeading.textContent = editingTrack
      ? `Editing song ${editingBatch + 1} of ${batchTracks.length}`
      : "Check the metadata";
  }
  fetchHeading.textContent = batchActive ? "Add another song" : "Add a song";
}

function selectBatchTrack(i) {
  if (mode !== "single" || !batchTracks[i]) return;
  commitFormToTarget();
  editingBatch = i;
  fillForm(batchTracks[i].meta);
  setCoverSrc(batchTracks[i].job_id);
  setSourceBadge(batchTracks[i].source || null);
  resultCard.classList.remove("hidden");
  loadAlbums($("f-artist").value.trim());
  updateModeClasses();
  renderBatchPanel();
  updatePreview();
}

function selectDraft() {
  if (!currentSongReady()) return;
  commitFormToTarget();
  editingBatch = null;
  fillForm(currentJob.metadata || {});
  setCover(currentJob);
  loadAlbums($("f-artist").value.trim());
  updateModeClasses();
  renderBatchPanel();
  updatePreview();
}

function renderBatchPanel() {
  const currentReady = currentSongReady();
  const total = batchTracks.length + (currentReady ? 1 : 0);
  // Keep the card visible while a message is shown (e.g. a save confirmation),
  // even after the queue itself has been cleared.
  batchCard.classList.toggle("hidden", total === 0 && !batchMessage.textContent);
  batchCount.textContent = `${total} song${total === 1 ? "" : "s"}`;

  batchList.innerHTML = "";
  for (let i = 0; i < batchTracks.length; i += 1) {
    const b = batchTracks[i];
    const li = document.createElement("li");
    li.className = "batch-item";
    if (mode === "single" && editingBatch === i) li.classList.add("selected");
    li.title = "Click to edit";
    const span = document.createElement("span");
    span.textContent = `${i + 1}. ${b.meta.title || "(untitled)"} — ${b.meta.artist || "Unknown Artist"}`;
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "batch-remove";
    rm.title = "Remove song";
    rm.textContent = "×";
    rm.addEventListener("click", (event) => {
      event.stopPropagation();
      const idx = batchTracks.indexOf(b);
      if (idx >= 0) batchTracks.splice(idx, 1);
      if (editingBatch !== null) {
        if (batchTracks.length === 0) {
          editingBatch = null;
        } else if (editingBatch > idx && editingBatch > 0) {
          editingBatch -= 1;
        } else if (editingBatch >= batchTracks.length) {
          editingBatch = batchTracks.length - 1;
        }
      }
      renderBatchPanel();
      if (editingBatch !== null && batchTracks[editingBatch]) {
        fillForm(batchTracks[editingBatch].meta);
        setCoverSrc(batchTracks[editingBatch].job_id);
      } else if (currentReady) {
        fillForm(currentJob.metadata || {});
        setCover(currentJob);
      }
      updateModeClasses();
      updatePreview();
    });
    li.appendChild(span);
    li.appendChild(rm);
    li.addEventListener("click", () => selectBatchTrack(i));
    batchList.appendChild(li);
  }

  if (currentReady) {
    const m = editingBatch === null ? currentMeta() : (currentJob.metadata || {});
    const li = document.createElement("li");
    li.className = "batch-item current";
    if (mode === "single" && editingBatch === null) li.classList.add("selected");
    li.title = "Click to edit";
    const span = document.createElement("span");
    span.textContent = `${batchTracks.length + 1}. ${m.title || "(untitled)"} — ${m.artist || "Unknown Artist"} (new)`;
    li.appendChild(span);
    li.addEventListener("click", () => selectDraft());
    batchList.appendChild(li);
  }

  if (total === 0) {
    batchSaveButton.textContent = "Save songs to library";
  } else if (total === 1) {
    batchSaveButton.textContent = "Save 1 song to library";
  } else {
    batchSaveButton.textContent = `Save ${total} songs to library`;
  }
  batchSaveButton.disabled = total === 0;
}

function resetBatch() {
  batchTracks = [];
  batchDefaults = emptyBatchDefaults();
  batchActive = false;
  editingBatch = null;
  resetApplyToggles();
  setBatchMessage("");
  updateModeClasses();
  renderBatchPanel();
}

function addCurrentToBatch() {
  if (!currentSongReady()) return;
  commitFormToTarget();
  batchTracks.push({ job_id: currentJob.id, meta: currentJob.metadata || currentMeta(), source: currentJob.source || null });
  batchActive = true;
  editingBatch = null;
  currentJob = null;
  jobId = null;
  clearTimeout(previewTimer);
  pathPreview.textContent = "—";
  resultCard.classList.add("hidden");
  setError(saveError, "");
  saveSuccess.classList.add("hidden");
  saveSuccess.textContent = "";
  setBatchMessage("");
  setSavedCard(null);
  $("url-input").value = "";
  updateModeClasses();
  renderBatchPanel();
  $("url-input").focus();
}

async function saveBatch() {
  commitFormToTarget();
  const tracks = [];
  for (const b of batchTracks) tracks.push(Object.assign({ job_id: b.job_id }, b.meta));
  if (currentSongReady() && currentJob) tracks.push(Object.assign({ job_id: currentJob.id }, currentJob.metadata || {}));
  if (!tracks.length) return;

  batchSaveButton.disabled = true;
  setBatchMessage("");
  try {
    const data = await api("/api/batch/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tracks })
    });
    const saved = data.tracks || [];
    batchTracks = [];
    batchDefaults = emptyBatchDefaults();
    batchActive = false;
    setBatchMessage("");
    resetAfterSave();
    setSavedCard(saved);
  } catch (err) {
    // Keep the queue so the failed save can be retried.
    setBatchMessage(err.message, "error");
    stopPolling();
    fetchButton.disabled = false;
  } finally {
    batchSaveButton.disabled = false;
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
  editingBatch = null;
  showWizard(false);
  showChaptersCard(!!(currentJob && currentJob.has_chapters));
  if (currentJob) {
    fillForm(currentJob.metadata || {});
    if (batchActive) applyBatchDefaults();
    setCover(currentJob);
  }
  updateModeClasses();
  renderBatchPanel();
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
    resetApplyToggles();
    showChaptersCard(false);
    showWizard(true);
    formHeading.textContent = `Check each track (${tracks.length} songs)`;
    updateModeClasses();
    renderBatchPanel();
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
    fetchButton.disabled = true;
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
    editingBatch = null;
    if (!batchActive) resetApplyToggles();
    fillForm(job.metadata || {});
    if (batchActive) applyBatchDefaults();
    setCover(job);
    setSourceBadge(job.source);
    resultCard.classList.remove("hidden");
    setError(saveError, "");
    saveSuccess.classList.add("hidden");
    saveSuccess.textContent = "";
    setBatchMessage("");
    setSavedCard(null);
    showWizard(false);
    formHeading.textContent = "Check the metadata";
    if (job.multi_track && Array.isArray(job.tracks) && job.tracks.length > 1) {
      // Spotify album/playlist: reuse the wizard to review and edit each track.
      tracks = job.tracks;
      mode = "tracks";
      trackIndex = 0;
      showChaptersCard(false);
      showWizard(true);
      formHeading.textContent = `Check each track (${tracks.length} songs)`;
      showTrack();
    } else if (job.has_chapters) {
      chaptersText.textContent = `This video has ${chapters.length} chapters. Save it as one track, or split it into ${chapters.length} songs and edit each one before saving.`;
      splitModeButton.textContent = `Split into ${chapters.length} songs`;
      showChaptersCard(true);
    } else {
      showChaptersCard(false);
    }
    updateModeClasses();
    renderBatchPanel();
    loadArtists();
    loadAlbums(($("f-artist").value || "").trim());
    updatePreview();
  }
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    if (!jobId) return;
    const pollingId = jobId;
    let job;
    try {
      job = await api(`/api/jobs/${pollingId}`);
    } catch (err) {
      stopPolling();
      if (jobId !== pollingId) return;
      showProgress(false);
      fetchButton.disabled = false;
      setError(fetchError, err.message);
      return;
    }
    // A cancel may have landed while this poll was in flight; ignore the
    // stale snapshot instead of redrawing the old job over a cleared screen.
    if (jobId !== pollingId) return;
    renderJob(job);
    if (job.status === "done" || job.status === "error" || job.status === "cancelled") {
      stopPolling();
      if (job.status === "error") {
        showProgress(false);
        fetchButton.disabled = false;
      }
    }
  }, 1500);
}

fetchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = $("url-input").value.trim();
  if (!url) return;
  // If a finished song is still on the form, queue it first so it is not lost
  // when the next fetch replaces it.
  if (mode === "single" && currentSongReady()) {
    commitFormToTarget();
    batchTracks.push({ job_id: currentJob.id, meta: currentJob.metadata || {} });
    batchActive = true;
    currentJob = null;
    jobId = null;
  }
  setError(fetchError, "");
  setError(saveError, "");
  setFetchSuccess("");
  setSavedCard(null);
  saveSuccess.classList.add("hidden");
  saveSuccess.textContent = "";
  resultCard.classList.add("hidden");
  clearTimeout(previewTimer);
  pathPreview.textContent = "—";
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
  if (mode === "single" && editingBatch !== null) return; // batch tracks save via the panel
  if (!jobId) return;
  saveButton.disabled = true;
  setError(saveError, "");
  saveSuccess.classList.add("hidden");
  saveSuccess.textContent = "";
  try {
    if (mode === "split" || mode === "tracks") {
      commitCurrentTrack();
      const endpoint = mode === "split" ? "save_many" : "save_tracks";
      const data = await api(`/api/jobs/${jobId}/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tracks })
      });
      const saved = data.tracks || [];
      resetAfterSave();
      setSavedCard(saved);
    } else {
      const data = await api(`/api/jobs/${jobId}/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentMeta())
      });
      resetAfterSave();
      // Single mode: the result card is hidden on reset, so use the line under
      // the fetch card for the one destination.
      setFetchSuccess(`Saved to ${data.relative}`);
    }
  } catch (err) {
    setError(saveError, err.message);
  } finally {
    saveButton.disabled = false;
  }
});

resetButton.addEventListener("click", async () => {
  // "Start over" may be pressed while a fetch is still running, so abort it
  // first; otherwise the download would keep going invisibly and then pop its
  // result card back up.
  if (jobId && (cancelling || fetchButton.disabled)) {
    await cancelFetch();
  }
  window.location.reload();
});

cancelButton.addEventListener("click", cancelFetch);

singleModeButton.addEventListener("click", enterSingleMode);
splitModeButton.addEventListener("click", enterSplitMode);
backSingle.addEventListener("click", enterSingleMode);
addToBatchButton.addEventListener("click", addCurrentToBatch);
batchSaveButton.addEventListener("click", saveBatch);
batchClearButton.addEventListener("click", resetBatch);

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

// Per-field "apply to all": checking a toggle copies the current value to every
// track (split mode) or every queued song (batch mode), and while it stays
// checked, edits keep everything in sync. Leave it off to edit only the
// selected track.
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
