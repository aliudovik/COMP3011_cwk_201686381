/* app/static/public.js — drVibey Chat + Auth + Generation Flow */

(function () {
  const $ = (id) => document.getElementById(id);

  // ── State ──────────────────────────────────────────────────────────
  let gUserId = null;
  let gIsAuthenticated = false;
  let gCurrentUser = null; // { id, email, display_name, photo_url, auth_provider }

  // Chat state
  let gChatHistory = [];           // [{role: "assistant"|"user", content: "..."}]
  let gCurrentQuestion = 0;        // which question was last ASKED (1-13)
  let gIsWaitingForReply = false;
  let gCurrentInputType = "text";  // "screenshot"|"chip_select"|"text"|"buttons"|"none"
  let gCurrentSkippable = false;

  // Screenshot state
  let gSelectedFiles = [];         // File objects

  // Chip select state (Q2)
  let gChipOptions = null;         // {artists: [...], songs: [...]}
  let gSelectedArtists = new Set();
  let gSelectedSongs = new Set();

  // Profile state
  let gExtractedTracks = [];
  let gProfileJson = null;
  let gDiagnosisJson = null;
  let gListenerProfileId = null;

  // Current result state
  let gCurrentGenId = null;
  let gCurrentGenIsFavourite = false;
  let gCurrentGenLikeStatus = null;   // null | "liked" | "disliked"
  let gCurrentGenCreatedAt = null;
  let gPlaybackTimerInterval = null;
  let gGenerationPollTimer = null;
  let gGenerationPollInFlight = false;
  let gGenerationPollStartedAt = 0;
  let gPlayerHasFinalAudio = false;
  let gPlayerPlaybackEnabled = false;

  // Generation state (existing flow)
  let gSelectedMoodId = null;
  let gSelectedMoodLabel = null;
  let gMoodIntensity = 0.5;
  let gSelectedActivityId = null;
  let gSelectedActivityLabel = null;
  let gInstrumental = true;
  let gSongReference = null;
  let gGenre = null;
  let gBpm = null;
  let gLanguage = "english";
  let gSurpriseMe = false;
  let gLoadingNarrationTimer = null;
  let gLoadingElapsedTimer = null;
  let gLoadingStartedAtMs = 0;
  let gAuthBackScreenId = "screenChat";
  let gCurrentScreenId = null;

  const SKIP_TOKEN = "[skipped]";
  const TOTAL_QUESTIONS = 10;
  const TYPE_CATALOG = {
    FVPD: { name: "Neon Oracle", description: "Big future-pop vocals, cinematic drops, emotional but explosive." },
    FVPR: { name: "Arena Pulse", description: "Anthemic hooks, confidence music, modern stadium energy." },
    FVCD: { name: "Holo Whisper", description: "Airy vocals, synth haze, gentle but transportive." },
    FVCR: { name: "Chrome Confessional", description: "Clean production, intimate lyrics, sleek and controlled." },
    FIPD: { name: "Circuit Titan", description: "Aggressive electronic/instrumental force, bass-forward and bold." },
    FIPR: { name: "Iron Groove", description: "Precision beats, rhythm obsession, engineered momentum." },
    FICD: { name: "Glass Drift", description: "Ambient textures, experimental calm, dreamy sonic architecture." },
    FICR: { name: "Metro Minimalist", description: "Sparse, modern, efficient, tasteful and highly curated." },
    NVPD: { name: "Velvet Prophet", description: "Soulful retro vocals with emotional weight and big feeling." },
    NVPR: { name: "Anthem Archivist", description: "Classic-era bangers, sing-alongs, timeless crowd energy." },
    NVCD: { name: "Moonlit Memoir", description: "Warm, sentimental, late-night memory soundtrack." },
    NVCR: { name: "Cassette Confessor", description: "Honest lyric-first songs, grounded and deeply human." },
    NIPD: { name: "Analog Storm", description: "Guitar/drum-driven force, raw intensity, old-school fire." },
    NIPR: { name: "Vinyl Vanguard", description: "Groove purist, craft-focused, timeless instrumental authority." },
    NICD: { name: "Lo-Fi Stargazer", description: "Soft instrumental nostalgia, dreamy and introspective." },
    NICR: { name: "Hearth Conductor", description: "Organic, cozy, grounded arrangements; calm and intentional." },
  };

  // ── Helpers ────────────────────────────────────────────────────────
  function show(el) { if (el) el.classList.remove("is-hidden"); }
  function hide(el) { if (el) el.classList.add("is-hidden"); }

  function showLoading(text, opts = {}) {
    const overlay = $("loadingOverlay");
    const t = $("loadingText");
    const sub = $("loadingSubtext");
    const eta = $("loadingEta");
    if (t) t.textContent = text || "Loading…";
    if (sub) {
      const subtext = opts.subtext || "";
      sub.textContent = subtext;
      if (subtext) show(sub); else hide(sub);
    }
    if (eta) {
      const etaText = opts.eta || "";
      eta.textContent = etaText;
      if (etaText) show(eta); else hide(eta);
    }
    if (overlay) { show(overlay); overlay.setAttribute("aria-hidden", "false"); }
  }

  function hideLoading() {
    stopLoadingNarration();
    const overlay = $("loadingOverlay");
    if (overlay) { hide(overlay); overlay.setAttribute("aria-hidden", "true"); }
  }

  function stopLoadingNarration() {
    if (gLoadingNarrationTimer) {
      clearInterval(gLoadingNarrationTimer);
      gLoadingNarrationTimer = null;
    }
    if (gLoadingElapsedTimer) {
      clearInterval(gLoadingElapsedTimer);
      gLoadingElapsedTimer = null;
    }
    gLoadingStartedAtMs = 0;
  }

  function startLoadingNarration(title, steps, etaHint) {
    stopLoadingNarration();
    gLoadingStartedAtMs = Date.now();

    const sequence = Array.isArray(steps) && steps.length ? steps : ["Working on it..."];
    let idx = 0;
    let currentStep = sequence[0];

    const render = () => {
      const elapsedSec = Math.max(0, Math.floor((Date.now() - gLoadingStartedAtMs) / 1000));
      const eta = etaHint || "Usually less than 15 seconds";
      showLoading(title, {
        subtext: currentStep,
        eta: `${eta} • ${elapsedSec}s elapsed`,
      });
    };

    const advanceStep = () => {
      currentStep = sequence[idx % sequence.length];
      idx += 1;
      render();
    };

    advanceStep();
    gLoadingNarrationTimer = setInterval(advanceStep, 3000);
    gLoadingElapsedTimer = setInterval(render, 1000);
  }

  function updateChatProgress(questionNumber) {
    const fill = $("chatProgressFill");
    const text = $("chatProgressText");
    const track = document.querySelector(".chat-progress-track");

    const q = Math.max(0, Math.min(TOTAL_QUESTIONS, Number(questionNumber) || 0));
    const pct = Math.round((q / TOTAL_QUESTIONS) * 100);

    if (fill) fill.style.width = pct + "%";
    if (text) text.textContent = `Question ${q} of ${TOTAL_QUESTIONS}`;
    if (track) track.setAttribute("aria-valuenow", String(q));
  }

  function clamp01(v, fallback = 0.5) {
    const n = Number(v);
    if (!isFinite(n)) return fallback;
    return Math.max(0, Math.min(1, n));
  }

  function pct(n) {
    return Math.round(clamp01(n, 0.5) * 100);
  }

  function formatSoulSignature(raw) {
    const clean = String(raw || "").replace(/\s+/g, " ").trim();
    if (!clean) return "";
    const words = clean.split(" ");
    if (words.length <= 95) return clean;
    return `${words.slice(0, 95).join(" ").trim()}…`;
  }

  function getTypeMeta(typeCode) {
    const code = String(typeCode || "").trim().toUpperCase();
    return TYPE_CATALOG[code] || {
      name: "Resonance Seeker",
      description: "Emotion-led taste with balanced sonic curiosity.",
    };
  }

  function normalizeListenerTypeCode(profile, diagnosis) {
    const raw = String(
      profile?.listener_persona?.listener_mbti_like ||
      diagnosis?.listener_mbti_like ||
      ""
    ).trim().toUpperCase();
    if (TYPE_CATALOG[raw]) return raw;

    const novelty = clamp01(profile?.discovery_drive, 0.5);
    const openness = clamp01(profile?.emotional_profile?.emotional_depth, 0.5);
    const intensity = clamp01(profile?.energy_range?.high, 0.5);
    const introspection = clamp01(profile?.emotional_profile?.emotional_depth, 0.5);

    const c1 = novelty >= 0.5 ? "F" : "N";
    const c2 = openness >= 0.5 ? "V" : "I";
    const c3 = intensity >= 0.5 ? "P" : "C";
    const c4 = introspection >= 0.5 ? "D" : "R";
    return `${c1}${c2}${c3}${c4}`;
  }

  function getVocalFocusDescriptor(profile, typeCode) {
    const orientation = String(profile?.listening_orientation || "").toLowerCase();
    const vocals = String(profile?.production_traits?.vocals || "").toLowerCase();
    const vibes = Array.isArray(profile?.vibe_keywords) ? profile.vibe_keywords.map((v) => String(v).toLowerCase()) : [];

    if (vocals.includes("breathy")) return "Breathy";
    if (vocals.includes("airy") || vibes.some((v) => v.includes("dream") || v.includes("haze"))) return "Airy";
    if (vocals.includes("deep") || vibes.some((v) => v.includes("dark") || v.includes("melanch"))) return "Deep";
    if (orientation === "lyrics" || orientation === "voice") return "Textured";
    if (typeCode && String(typeCode).charAt(1) === "I") return "Minimal";
    return "Textured";
  }

  function getPowerPct(profile, typeCode) {
    if (String(typeCode || "").charAt(2) === "P") return 82;
    if (String(typeCode || "").charAt(2) === "C") return 38;
    return pct(profile?.energy_range?.high || 0.5);
  }

  function getNostalgiaPct(profile, typeCode) {
    const code = String(typeCode || "");
    if (code.charAt(0) === "N") return 78;
    if (code.charAt(0) === "F") return 34;
    return pct(profile?.emotional_profile?.emotional_depth || 0.5);
  }

  function renderTypeCatalog() {
    const wrap = $("diagTypeCatalog");
    if (!wrap) return;
    wrap.innerHTML = "";
    Object.keys(TYPE_CATALOG)
      .sort()
      .forEach((code) => {
        const item = TYPE_CATALOG[code];
        const row = document.createElement("div");
        row.className = "diag-type-item";

        const c = document.createElement("div");
        c.className = "diag-type-item-code";
        c.textContent = code;

        const n = document.createElement("div");
        n.className = "diag-type-item-name";
        n.textContent = item.name;

        const d = document.createElement("div");
        d.className = "diag-type-item-desc";
        d.textContent = item.description;

        row.appendChild(c);
        row.appendChild(n);
        row.appendChild(d);
        wrap.appendChild(row);
      });
  }

  function openTypeModal() {
    const modal = $("diagTypeModal");
    if (!modal) return;
    show(modal);
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
  }

  function closeTypeModal() {
    const modal = $("diagTypeModal");
    if (!modal) return;
    hide(modal);
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
  }

  function setChatProgressVisibility(isVisible) {
    const progress = document.querySelector(".chat-progress");
    if (!progress) return;
    progress.classList.toggle("is-temporarily-hidden", !isVisible);
  }

  function msToTime(secs) {
    if (!isFinite(secs)) return "00:00";
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function isGenerationExpired(gen) {
    if (!gen || !gen.created_at) return false;
    const createdAt = new Date(gen.created_at);
    if (Number.isNaN(createdAt.getTime())) return false;
    return (Date.now() - createdAt.getTime()) > (90 * 60 * 1000);
  }

  function refreshPlayerTimeline() {
    const audioEl = $("audioEl");
    const seek = $("seek");
    const tCur = $("tCur");
    const tDur = $("tDur");
    const timeline = seek ? seek.closest(".timeline") : null;
    if (!audioEl) return;

    const current = audioEl.currentTime || 0;
    const duration = isFinite(audioEl.duration) && audioEl.duration > 0 ? audioEl.duration : 0;

    if (tCur) tCur.textContent = msToTime(current);

    if (seek) {
      seek.classList.toggle("is-stream-locked", !gPlayerHasFinalAudio && gPlayerPlaybackEnabled);
      seek.classList.toggle("is-expired", !gPlayerPlaybackEnabled);
      if (duration > 0) {
        seek.value = String((current / duration) * 100);
        seek.disabled = !gPlayerPlaybackEnabled;
      } else {
        seek.value = "0";
        seek.disabled = true;
      }
    }

    if (timeline) {
      timeline.classList.toggle("is-stream-locked", !gPlayerHasFinalAudio && gPlayerPlaybackEnabled);
      timeline.classList.toggle("is-expired", !gPlayerPlaybackEnabled);
    }

    if (tDur) {
      if (!gPlayerPlaybackEnabled) {
        tDur.textContent = "expired";
      } else if (!gPlayerHasFinalAudio) {
        tDur.textContent = "stream";
      } else {
        tDur.textContent = duration > 0 ? msToTime(duration) : "00:00";
      }
    }
  }

  function copyTextToClipboard(text) {
    const value = String(text || "");
    if (!value) return Promise.resolve(false);

    if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
      return navigator.clipboard.writeText(value).then(() => true).catch(() => false);
    }

    return new Promise((resolve) => {
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.top = "-9999px";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, ta.value.length);

      let ok = false;
      try {
        ok = document.execCommand("copy");
      } catch (_e) {
        ok = false;
      }

      document.body.removeChild(ta);
      resolve(ok);
    });
  }

  async function shareProfile() {
    const statusEl = $("diagShareStatus");

    if (statusEl) {
      statusEl.textContent = "Creating share link...";
      show(statusEl);
    }
    try {
      const resp = await fetch("/api/profile/share", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: gUserId,
          listener_profile_id: gListenerProfileId,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok || !data.share_url) {
        throw new Error(data.error || "Failed to create share link");
      }
      const url = data.share_url;
      const copied = await copyTextToClipboard(url);
      if (statusEl) {
        statusEl.textContent = copied
          ? "Share link ready. Copied to clipboard."
          : "Share link ready. Copy might be blocked on this browser.";
        show(statusEl);
      }
    } catch (e) {
      if (statusEl) {
        statusEl.textContent = "Could not create share link right now.";
        show(statusEl);
      }
      console.error("Share profile error:", e);
    }
  }

  function switchScreen(screenId) {
    const screens = [
      "screenLogin", "screenRegister", "screenHome", "screenFavourites", "screenChat", "screenProfile",
      "screenPsychProfile",
      "screenMood", "screenActivity", "screenExtras", "screenGenerating", "screenResult",
    ];
    screens.forEach((id) => {
      const el = $(id);
      if (el) { if (id === screenId) show(el); else hide(el); }
    });
    gCurrentScreenId = screenId;
  }
  // Expose switchScreen globally for psychoacoustic.js
  window.switchScreen = switchScreen;

  function openAuthScreen(screenId) {
    if (gCurrentScreenId && gCurrentScreenId !== "screenLogin" && gCurrentScreenId !== "screenRegister") {
      gAuthBackScreenId = gCurrentScreenId;
    }
    if (gCurrentScreenId === "screenResult" && gCurrentGenId) {
      try { sessionStorage.setItem("_auth_return_gen", String(gCurrentGenId)); } catch (e) {}
    }
    switchScreen(screenId);
  }

  function navigateBackFromAuth() {
    try { sessionStorage.removeItem("_auth_return_gen"); } catch (e) {}
    const target = gAuthBackScreenId || "screenChat";
    switchScreen(target);
    if (target === "screenChat") initChat();
  }

  /** Scroll the chat area to the very bottom so the latest message is visible */
  function scrollChatToBottom() {
    const container = $("chatMessages");
    if (container) {
      container.scrollTop = container.scrollHeight;
      setTimeout(() => { container.scrollTop = container.scrollHeight; }, 60);
    }
    const section = $("screenChat");
    if (section) {
      section.scrollTop = section.scrollHeight;
      setTimeout(() => { section.scrollTop = section.scrollHeight; }, 60);
    }
  }

  /** Hide all input areas, then show the correct one based on input_type */
  function showInputForType(inputType, skippable) {
    gCurrentInputType = inputType;
    gCurrentSkippable = skippable;
    const activeEl = document.activeElement;
    if (activeEl && typeof activeEl.blur === "function") activeEl.blur();

    hide($("chatInputWrap"));
    hide($("imageUploadWrap"));
    hide($("chipSelectWrap"));
    hide($("buttonSelectWrap"));
    hide($("multiButtonSelectWrap"));
    hide($("skipWrap"));
    hide($("pathChoiceWrap"));
    setChatProgressVisibility(true);

    switch (inputType) {
      case "screenshot":
        show($("imageUploadWrap"));
        break;
      case "chip_select":
        show($("chipSelectWrap"));
        setChatProgressVisibility(false);
        break;
      case "text":
        show($("chatInputWrap"));
        $("chatInput").focus();
        if (skippable) show($("skipWrap"));
        break;
      case "buttons":
        show($("buttonSelectWrap"));
        break;
      case "multi_buttons":
        show($("multiButtonSelectWrap"));
        break;
      case "none":
        break;
    }

    scrollChatToBottom();
  }

  // ── Auth helpers ───────────────────────────────────────────────────
  function updateHeaderUI() {
    // Profile icon is always visible; no toggle needed
  }

  function openSideMenu() {
    const menu = $("dropdownMenu");
    const backdrop = $("sideMenuBackdrop");
    if (menu) menu.classList.add("is-open");
    if (backdrop) backdrop.classList.add("is-active");
  }

  function closeSideMenu() {
    const menu = $("dropdownMenu");
    const backdrop = $("sideMenuBackdrop");
    if (menu) menu.classList.remove("is-open");
    if (backdrop) backdrop.classList.remove("is-active");
  }

  function toggleDropdown() {
    const menu = $("dropdownMenu");
    if (menu && menu.classList.contains("is-open")) {
      closeSideMenu();
    } else {
      openSideMenu();
    }
  }

  function closeDropdown() {
    closeSideMenu();
  }

  async function checkAuthState() {
    try {
      const resp = await fetch("/api/auth/me");
      const data = await resp.json();

      if (data.ok && data.is_authenticated && data.user) {
        gIsAuthenticated = true;
        gCurrentUser = data.user;
        gUserId = data.user.id;
        updateHeaderUI();
        return true;
      }
    } catch (e) {
      console.error("Auth check error:", e);
    }

    gIsAuthenticated = false;
    gCurrentUser = null;
    updateHeaderUI();
    return false;
  }

  /**
   * Start server-side Google OAuth flow.
   * The browser returns to "/" with an authenticated session cookie.
   * @param {string} [redirectDest] - desired post-auth screen
   */
  async function signInWithGoogle(redirectDest) {
    try {
      if (redirectDest) {
        sessionStorage.setItem("_auth_dest", redirectDest);
      } else {
        sessionStorage.removeItem("_auth_dest");
      }
    } catch (e) {
      // sessionStorage may be unavailable in private mode
    }
    window.location.href = "/auth/google-signin";
    return false;
  }

  /**
   * Validate password strength for email/password auth.
   * Requires at least 7 characters and at least one digit.
   * @param {string} password
   * @returns {boolean}
   */
  function isValidPassword(password) {
    return typeof password === "string" && password.length >= 7 && /\d/.test(password);
  }

  /**
   * Verify Firebase ID token with backend and set authenticated UI state.
   * @param {string} idToken
   * @returns {Promise<boolean>}
   */
  async function verifyFirebaseTokenWithBackend(idToken) {
    try {
      const resp = await fetch("/api/auth/verify-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id_token: idToken }),
      });
      const data = await resp.json();

      if (data.ok && data.user) {
        gIsAuthenticated = true;
        gCurrentUser = data.user;
        gUserId = data.user.id;
        updateHeaderUI();
        return true;
      }
    } catch (e) {
      console.error("Token verification error:", e);
    }
    return false;
  }

  /**
   * Register or sign in with Firebase email/password auth.
   * If email is already registered, falls back to sign-in.
   * @param {string} email
   * @param {string} password
   * @returns {Promise<boolean>}
   */
  async function loginWithEmail(email, password) {
    if (typeof firebase === "undefined" || !firebase.auth) {
      showAuthMsg("loginEmailError", "Firebase is not configured yet. Please check back later.");
      return false;
    }
    try {
      showLoading("Signing in...");
      const cred = await firebase.auth().signInWithEmailAndPassword(email, password);
      const idToken = await cred.user.getIdToken();
      const ok = await verifyFirebaseTokenWithBackend(idToken);
      hideLoading();
      if (!ok) { showAuthMsg("loginEmailError", "Authentication failed. Please try again."); return false; }
      showAuthMsg("loginEmailSuccess", "Signed in successfully.");
      return true;
    } catch (e) {
      hideLoading();
      console.error("Email login error:", e);
      const msg = {
        "auth/invalid-email": "Please enter a valid email address.",
        "auth/wrong-password": "Incorrect password. Please try again.",
        "auth/invalid-credential": "Incorrect email or password.",
        "auth/user-not-found": "No account found for this email. Try registering instead.",
        "auth/too-many-requests": "Too many attempts. Please wait and try again.",
      }[e.code] || "Sign-in failed. Please check your details and try again.";
      showAuthMsg("loginEmailError", msg);
      return false;
    }
  }

  async function registerWithEmail(email, password) {
    if (typeof firebase === "undefined" || !firebase.auth) {
      showAuthMsg("registerEmailError", "Firebase is not configured yet. Please check back later.");
      return false;
    }
    try {
      showLoading("Creating account...");
      const cred = await firebase.auth().createUserWithEmailAndPassword(email, password);
      const idToken = await cred.user.getIdToken();
      const ok = await verifyFirebaseTokenWithBackend(idToken);
      hideLoading();
      if (!ok) { showAuthMsg("registerEmailError", "Authentication failed. Please try again."); return false; }
      showAuthMsg("registerEmailSuccess", "Account created successfully.");
      return true;
    } catch (e) {
      hideLoading();
      console.error("Email registration error:", e);
      const msg = {
        "auth/invalid-email": "Please enter a valid email address.",
        "auth/weak-password": "Password must be at least 7 characters and include at least 1 number.",
        "auth/email-already-in-use": "An account with this email already exists. Try logging in instead.",
        "auth/too-many-requests": "Too many attempts. Please wait and try again.",
      }[e.code] || "Registration failed. Please check your details and try again.";
      showAuthMsg("registerEmailError", msg);
      return false;
    }
  }

  function showAuthMsg(id, msg) {
    const el = $(id);
    if (!el) return;
    el.textContent = msg;
    show(el);
    const isError = id.includes("Error");
    const sibling = $(id.replace(isError ? "Error" : "Success", isError ? "Success" : "Error"));
    if (sibling) { sibling.textContent = ""; hide(sibling); }
  }

  function clearAuthMsgs(prefix) {
    const err = $(prefix + "Error");
    if (err) { err.textContent = ""; hide(err); }
    const suc = $(prefix + "Success");
    if (suc) { suc.textContent = ""; hide(suc); }
  }

  /**
   * Load the user's saved profile/diagnosis from the database.
   * Populates gProfileJson and gDiagnosisJson so "My Vibe" works after reload.
   */
  async function loadSavedProfile() {
    try {
      const resp = await fetch("/api/profile");
      const data = await resp.json();
      if (data.ok && data.has_profile) {
        gProfileJson = data.profile;
        gDiagnosisJson = data.diagnosis;
        gListenerProfileId = data.listener_profile_id;
        console.log("[Profile] loaded saved profile, id:", data.listener_profile_id);
      }
    } catch (e) {
      console.error("Load profile error:", e);
    }
  }

  async function signOut() {
    try {
      if (typeof firebase !== "undefined" && firebase.auth) {
        await firebase.auth().signOut();
      }
      await fetch("/api/auth/logout", { method: "POST" });
    } catch (e) {
      console.error("Sign-out error:", e);
    }

    gIsAuthenticated = false;
    gCurrentUser = null;
    updateHeaderUI();
    closeDropdown();
    window.location.reload();
  }

  // ── Home screen (recent generations) ───────────────────────────────
  async function loadRecentGenerations() {
    const listEl = $("genList");
    const emptyEl = $("genEmpty");
    if (!listEl) return;

    listEl.innerHTML = "";

    try {
      const resp = await fetch("/api/generations");
      const data = await resp.json();

      if (data.ok && data.generations && data.generations.length > 0) {
        hide(emptyEl);
        data.generations.forEach((gen) => {
          const item = document.createElement("div");
          item.className = "gen-list-item";
          item.dataset.genId = gen.id;

          const thumb = document.createElement("div");
          thumb.className = "gen-thumb";
          if (gen.cover_url) {
            const img = document.createElement("img");
            img.src = gen.cover_url;
            img.alt = gen.title || "Cover";
            thumb.appendChild(img);
          } else {
            thumb.innerHTML = '<span class="gen-thumb-placeholder">\u266B</span>';
          }

          const info = document.createElement("div");
          info.className = "gen-info";

          const title = document.createElement("div");
          title.className = "gen-title";
          title.textContent = gen.title || "Untitled Track";

          const meta = document.createElement("div");
          meta.className = "gen-meta";
          const parts = [];
          if (gen.mood) parts.push(gen.mood);
          if (gen.activity) parts.push(gen.activity);
          if (gen.status && gen.status !== "succeeded") parts.push(gen.status);
          meta.textContent = parts.join(" \u00B7 ") || "\u2014";

          info.appendChild(title);
          info.appendChild(meta);

          item.appendChild(thumb);
          item.appendChild(info);

          // Heart indicator for favourited items
          if (gen.is_favourite) {
            const heart = document.createElement("div");
            heart.className = "gen-fav-heart";
            heart.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z"/></svg>';
            item.appendChild(heart);
          }

          // Click to open full result
          if (gen.status === "succeeded") {
            item.style.cursor = "pointer";
            item.addEventListener("click", () => {
              renderResultFromGen(gen);
              switchScreen("screenResult");
            });
          }

          listEl.appendChild(item);
        });
      } else {
        show(emptyEl);
      }
    } catch (e) {
      console.error("Load generations error:", e);
      show(emptyEl);
    }
  }

  /** Render result screen from a generation list item */
  function renderResultFromGen(gen) {
    gCurrentGenId = gen.id;
    gCurrentGenIsFavourite = !!gen.is_favourite;
    gCurrentGenLikeStatus = gen.like_status || null;
    gCurrentGenCreatedAt = gen.created_at || null;
    updateFavButton();
    updateLikeButtons();

    const moodLabel = $("resultMood");
    const audioEl = $("audioEl");
    const coverImg = $("coverImg");
    const resultTitle = document.querySelector(".result-title");

    if (resultTitle) resultTitle.textContent = gen.title || "YOUR SONG";
    if (moodLabel) moodLabel.textContent = "Mood: " + (gen.mood || "\u2014");

    if (coverImg && gen.cover_url) coverImg.src = gen.cover_url;

    const isExpired = isGenerationExpired(gen);
    const playbackUrl = isExpired ? null : gen.audio_url;
    if (audioEl && playbackUrl) {
      audioEl.src = playbackUrl;
      audioEl.load();
    } else if (audioEl) {
      audioEl.pause();
      audioEl.removeAttribute("src");
      audioEl.load();
    }

    setupPlayerListeners({ hasFinalAudio: false, canPlay: !!playbackUrl });
    startPlaybackTimer(gen.created_at);
    renderSimilarSongs(gen);
  }

  async function loadGenerationById(genId) {
    try {
      const resp = await fetch("/api/generation/" + genId);
      const data = await resp.json();
      if (data.ok && data.status === "succeeded") {
        gCurrentGenId = genId;
        gCurrentGenIsFavourite = !!data.is_favourite;
        gCurrentGenLikeStatus = data.like_status || null;
        gCurrentGenCreatedAt = data.created_at || null;
        updateFavButton();
        updateLikeButtons();
        renderResult(data.result);
        startPlaybackTimer(data.created_at);
        renderSimilarSongs(data.result);
        switchScreen("screenResult");
        return;
      }
    } catch (e) {
      console.error("Load generation error:", e);
    }
    loadSavedProfile();
    loadRecentGenerations();
    switchScreen("screenHome");
  }

  // ── Favourites ─────────────────────────────────────────────────────

  function updateFavButton() {
    const favBtn = $("favBtn");
    if (!favBtn) return;
    if (gCurrentGenIsFavourite) {
      favBtn.classList.add("is-favourited");
      favBtn.setAttribute("aria-label", "Remove from favourites");
    } else {
      favBtn.classList.remove("is-favourited");
      favBtn.setAttribute("aria-label", "Add to favourites");
    }
  }

  async function toggleFavourite() {
    if (!gIsAuthenticated) {
      openAuthScreen("screenLogin");
      return;
    }
    if (!gCurrentGenId) return;

    const newState = !gCurrentGenIsFavourite;

    try {
      const resp = await fetch("/api/generation/" + gCurrentGenId + "/favourite", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_favourite: newState }),
      });
      const data = await resp.json();
      if (data.ok) {
        gCurrentGenIsFavourite = data.is_favourite;
        updateFavButton();
      }
    } catch (e) {
      console.error("Toggle favourite error:", e);
    }
  }

  // ── Like / Dislike ────────────────────────────────────────────────

  function updateLikeButtons() {
    const likeBtn = $("likeBtn");
    const dislikeBtn = $("dislikeBtn");
    if (likeBtn) {
      likeBtn.classList.toggle("is-liked", gCurrentGenLikeStatus === "liked");
      likeBtn.setAttribute("aria-pressed", gCurrentGenLikeStatus === "liked" ? "true" : "false");
    }
    if (dislikeBtn) {
      dislikeBtn.classList.toggle("is-disliked", gCurrentGenLikeStatus === "disliked");
      dislikeBtn.setAttribute("aria-pressed", gCurrentGenLikeStatus === "disliked" ? "true" : "false");
    }
  }

  async function toggleLike(status) {
    if (!gIsAuthenticated) {
      openAuthScreen("screenLogin");
      return;
    }
    if (!gCurrentGenId) return;
    const newStatus = gCurrentGenLikeStatus === status ? null : status;

    try {
      const resp = await fetch("/api/generation/" + gCurrentGenId + "/like", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ like_status: newStatus }),
      });
      const data = await resp.json();
      if (data.ok) {
        gCurrentGenLikeStatus = data.like_status;
        updateLikeButtons();
      }
    } catch (e) {
      console.error("Toggle like error:", e);
    }
  }

  // ── Similar songs rendering ───────────────────────────────────────

  function renderSimilarSongs(data) {
    const wrap = $("similarSongs");
    const list = $("similarSongsList");
    if (!wrap || !list) return;

    let songs = null;
    if (data && data.similar_songs) {
      songs = data.similar_songs;
    } else if (data && data.result_json && data.result_json.similar_songs) {
      songs = data.result_json.similar_songs;
    }

    if (!Array.isArray(songs) || songs.length === 0) {
      wrap.classList.add("is-hidden");
      list.innerHTML = "";
      return;
    }

    list.innerHTML = "";
    songs.forEach((s) => {
      const el = document.createElement("div");
      el.className = "similar-song-item";
      const artist = s.artist || "Unknown Artist";
      const title = s.title || "Unknown Track";
      el.innerHTML = '<span class="similar-song-note">\u266B</span> ' +
        '<span class="similar-song-artist">' + artist + '</span>' +
        ' &mdash; ' +
        '<span class="similar-song-title">' + title + '</span>';
      list.appendChild(el);
    });
    wrap.classList.remove("is-hidden");
  }

  // ── Playback countdown timer ──────────────────────────────────────

  function startPlaybackTimer(createdAtStr) {
    const timerEl = $("playbackTimer");
    if (!timerEl) return;

    if (gPlaybackTimerInterval) {
      clearInterval(gPlaybackTimerInterval);
      gPlaybackTimerInterval = null;
    }

    if (!createdAtStr) {
      timerEl.textContent = "";
      return;
    }

    const createdAt = new Date(createdAtStr);
    if (Number.isNaN(createdAt.getTime())) {
      timerEl.textContent = "";
      return;
    }

    const WINDOW_MS = 90 * 60 * 1000;

    function tick() {
      const remaining = (createdAt.getTime() + WINDOW_MS) - Date.now();
      if (remaining <= 0) {
        timerEl.textContent = "Playback expired";
        timerEl.classList.add("is-expired");
        if (gPlaybackTimerInterval) {
          clearInterval(gPlaybackTimerInterval);
          gPlaybackTimerInterval = null;
        }
        // Disable playback live
        gPlayerPlaybackEnabled = false;
        const playBtn = $("playPauseBtn");
        const audioEl = $("audioEl");
        if (playBtn) {
          playBtn.disabled = true;
          playBtn.classList.add("is-disabled");
          playBtn.textContent = "\u25B6";
        }
        if (audioEl && !audioEl.paused) {
          audioEl.pause();
        }
        refreshPlayerTimeline();
        return;
      }
      const mins = Math.floor(remaining / 60000);
      const secs = Math.floor((remaining % 60000) / 1000);
      timerEl.textContent = "Playback expires in " +
        String(mins).padStart(2, "0") + ":" + String(secs).padStart(2, "0");
      timerEl.classList.remove("is-expired");
    }

    tick();
    gPlaybackTimerInterval = setInterval(tick, 1000);
  }

  async function loadFavourites() {
    const listEl = $("favList");
    const emptyEl = $("favEmpty");
    if (!listEl) return;

    listEl.innerHTML = "";

    try {
      const resp = await fetch("/api/generations/favourites");
      const data = await resp.json();

      if (data.ok && data.generations && data.generations.length > 0) {
        hide(emptyEl);
        data.generations.forEach((gen) => {
          const item = document.createElement("div");
          item.className = "gen-list-item";
          item.dataset.genId = gen.id;

          const thumb = document.createElement("div");
          thumb.className = "gen-thumb";
          if (gen.cover_url) {
            const img = document.createElement("img");
            img.src = gen.cover_url;
            img.alt = gen.title || "Cover";
            thumb.appendChild(img);
          } else {
            thumb.innerHTML = '<span class="gen-thumb-placeholder">\u266B</span>';
          }

          const info = document.createElement("div");
          info.className = "gen-info";

          const title = document.createElement("div");
          title.className = "gen-title";
          title.textContent = gen.title || "Untitled Track";

          const meta = document.createElement("div");
          meta.className = "gen-meta";
          const parts = [];
          if (gen.mood) parts.push(gen.mood);
          if (gen.activity) parts.push(gen.activity);
          meta.textContent = parts.join(" \u00B7 ") || "\u2014";

          info.appendChild(title);
          info.appendChild(meta);

          item.appendChild(thumb);
          item.appendChild(info);

          // Heart indicator (always shown on favourites page)
          const heart = document.createElement("div");
          heart.className = "gen-fav-heart";
          heart.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z"/></svg>';
          item.appendChild(heart);

          // Click to open full result
          if (gen.status === "succeeded") {
            item.style.cursor = "pointer";
            item.addEventListener("click", () => {
              renderResultFromGen(gen);
              switchScreen("screenResult");
            });
          }

          listEl.appendChild(item);
        });
      } else {
        show(emptyEl);
      }
    } catch (e) {
      console.error("Load favourites error:", e);
      show(emptyEl);
    }
  }

  // ── My Vibe (profile screen) ────────────────────────────────────────
  async function showMyVibe() {
    // If profile not in memory, fetch from server
    if (!gProfileJson || !gDiagnosisJson) {
      showLoading("Loading your vibe...");
      try {
        const resp = await fetch("/api/profile");
        const data = await resp.json();
        if (data.ok && data.has_profile) {
          gProfileJson = data.profile;
          gDiagnosisJson = data.diagnosis;
          gListenerProfileId = data.listener_profile_id;
        }
      } catch (e) {
        console.error("Load profile error:", e);
      }
      hideLoading();
    }

    if (gProfileJson && gDiagnosisJson) {
      // Route to correct profile screen based on profile type
      if (gProfileJson.profile_type === "psychoacoustic" && window.PsychoacousticTest) {
        window.PsychoacousticTest.renderResult(gDiagnosisJson);
      } else {
        renderDiagnosis(gProfileJson, gDiagnosisJson);
        hide($("profileCta"));
        show($("profileProceedBtn"));
        switchScreen("screenProfile");
      }
    } else {
      // No profile exists yet — start a session
      alert("No vibe profile found yet. Let\u2019s create one!");
      startNewSession();
    }
  }

  // ── Dropdown menu actions ──────────────────────────────────────────
  function handleDropdownAction(action) {
    closeDropdown();

    switch (action) {
      case "my-vibe":
        showMyVibe();
        break;

      case "new-session":
        startNewSession();
        break;

      case "generate":
        switchScreen("screenMood");
        setTimeout(() => autoSelectMood(), 100);
        break;

      case "home":
        loadRecentGenerations();
        switchScreen("screenHome");
        break;

      case "favourites":
        loadFavourites();
        switchScreen("screenFavourites");
        break;

      case "logout":
        signOut();
        break;
    }
  }

  function startNewSession() {
    // Reset chat state for a new session
    gChatHistory = [];
    gCurrentQuestion = 0;
    gIsWaitingForReply = false;
    gSelectedFiles = [];
    gChipOptions = null;
    gSelectedArtists.clear();
    gSelectedSongs.clear();
    gExtractedTracks = [];
    gChatInitData = null;
    updateChatProgress(0);

    // Clear chat messages
    const chatMessages = $("chatMessages");
    if (chatMessages) chatMessages.innerHTML = "";

    // Reset image upload
    const previewRow = $("imagePreviewRow");
    if (previewRow) previewRow.innerHTML = "";
    hide($("imageCount"));
    hide($("imageSubmitBtn"));

    // Reset psychoacoustic test UI
    hide($("psychTestWrap"));
    hide($("pathChoiceWrap"));
    show(chatMessages);
    const chatProgress = document.querySelector(".chat-progress");
    if (chatProgress) chatProgress.classList.remove("is-temporarily-hidden");

    switchScreen("screenChat");
    initChat();
  }

  // ── Chat rendering ─────────────────────────────────────────────────
  function addChatBubble(role, text) {
    const container = $("chatMessages");
    if (!container) return;

    const bubble = document.createElement("div");
    bubble.className = "chat-bubble chat-" + role;
    bubble.textContent = text;
    container.appendChild(bubble);

    scrollChatToBottom();
  }

  function addTypingIndicator() {
    const container = $("chatMessages");
    if (!container) return;

    const indicator = document.createElement("div");
    indicator.className = "chat-bubble chat-assistant chat-typing";
    indicator.id = "typingIndicator";
    indicator.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
    container.appendChild(indicator);
    scrollChatToBottom();
  }

  function removeTypingIndicator() {
    const el = $("typingIndicator");
    if (el) el.remove();
  }

  // ── Chat API calls ─────────────────────────────────────────────────
  let gChatInitData = null; // cached first-question data for quick path

  async function initChat() {
    addTypingIndicator();

    try {
      const resp = await fetch("/api/chat/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ init: true }),
      });
      const data = await resp.json();

      removeTypingIndicator();

      if (data.ok) {
        gChatInitData = data; // cache for quick path

        if (data.intro) {
          addChatBubble("assistant", data.intro);
          gChatHistory.push({ role: "assistant", content: data.intro });
        }

        // Show path choice instead of jumping to Q1
        addChatBubble("assistant", "Choose your path:");
        gChatHistory.push({ role: "assistant", content: "Choose your path:" });
        showInputForType("none", false);
        show($("pathChoiceWrap"));
      }
    } catch (e) {
      removeTypingIndicator();
      console.error("Chat init error:", e);
      const fallbackIntro = "Hey, I'm drVibey \u2014 your personal music psychologist. I'm here to heal you through the power of music... but first, I need to understand your diagnosis.";
      addChatBubble("assistant", fallbackIntro);
      gChatHistory.push({ role: "assistant", content: fallbackIntro });
      addChatBubble("assistant", "Choose your path:");
      gChatHistory.push({ role: "assistant", content: "Choose your path:" });
      showInputForType("none", false);
      show($("pathChoiceWrap"));
    }
  }

  function startQuickAnalysis() {
    hide($("pathChoiceWrap"));
    addChatBubble("user", "Quick Analysis");
    gChatHistory.push({ role: "user", content: "Quick Analysis" });

    // Use cached init data to show Q1
    const data = gChatInitData;
    if (data) {
      gCurrentQuestion = data.question_number;
      updateChatProgress(gCurrentQuestion);
      addChatBubble("assistant", data.reply);
      gChatHistory.push({ role: "assistant", content: data.reply });
      showInputForType(data.input_type || "screenshot", data.skippable || false);
    } else {
      // Fallback if no cached data
      const fallbackQ1 = "Drop me 3-10 screenshots of your favorite playlists, most-played songs, or your Year Wrapped from Spotify, Apple Music, YouTube Music \u2014 whatever you use.";
      gCurrentQuestion = 1;
      updateChatProgress(gCurrentQuestion);
      addChatBubble("assistant", fallbackQ1);
      gChatHistory.push({ role: "assistant", content: fallbackQ1 });
      showInputForType("screenshot", false);
    }
  }

  function startFullTest() {
    hide($("pathChoiceWrap"));
    addChatBubble("user", "The Full Test");
    gChatHistory.push({ role: "user", content: "The Full Test" });
    addChatBubble("assistant", "Excellent choice. Let's dive deep into your psychoacoustic profile. You'll hear pairs of audio clips and answer some questions about your preferences. Take your time \u2014 there are no wrong answers.");
    gChatHistory.push({ role: "assistant", content: "Excellent choice. Let's dive deep..." });

    // Start the psychoacoustic test
    if (window.PsychoacousticTest) {
      window.PsychoacousticTest.start();
    }
  }

  // Expose for psychoacoustic.js to set profile data after submission
  window.setPsychProfileData = function (data) {
    gListenerProfileId = data.listener_profile_id;
    gProfileJson = data.profile;
    gDiagnosisJson = data.diagnosis;
  };

  /** Send a user answer (text, skip, chip picks, or button pick) and advance */
  async function sendUserAnswer(text) {
    if (gIsWaitingForReply) return;

    const displayText = text === SKIP_TOKEN ? "(skipped)" : text;
    addChatBubble("user", displayText);
    gChatHistory.push({ role: "user", content: text });

    const input = $("chatInput");
    if (input) { input.value = ""; input.style.height = "auto"; }

    showInputForType("none", false);

    gIsWaitingForReply = true;

    if (gCurrentQuestion >= TOTAL_QUESTIONS) {
      gIsWaitingForReply = false;
      updateChatProgress(TOTAL_QUESTIONS);
      addChatBubble("assistant", "Got it! Let me build your musical DNA...");
      gChatHistory.push({ role: "assistant", content: "Got it! Let me build your musical DNA..." });
      await buildProfile();
      return;
    }

    try {
      const resp = await fetch("/api/chat/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_message: text,
          current_question: gCurrentQuestion,
        }),
      });
      const data = await resp.json();
      gIsWaitingForReply = false;

      if (data.ok) {
        if (data.is_complete) {
          addChatBubble("assistant", data.reply);
          gChatHistory.push({ role: "assistant", content: data.reply });
          await buildProfile();
          return;
        }

        gCurrentQuestion = data.question_number;
        updateChatProgress(gCurrentQuestion);
        addChatBubble("assistant", data.reply);
        gChatHistory.push({ role: "assistant", content: data.reply });

        if (data.input_type === "buttons" && data.button_options) {
          renderButtonOptions(data.button_options);
        }
        if (data.input_type === "multi_buttons" && data.button_groups) {
          renderMultiButtonOptions(data.button_groups);
        }
        showInputForType(data.input_type, data.skippable || false);
      } else {
        addChatBubble("assistant", "Hmm, something went wrong. Try again?");
        showInputForType("text", true);
      }
    } catch (e) {
      gIsWaitingForReply = false;
      console.error("Chat send error:", e);
      addChatBubble("assistant", "Connection issue. Try sending that again.");
      showInputForType("text", true);
    }
  }

  // ── Screenshot upload ──────────────────────────────────────────────
  function updateImagePreview() {
    const row = $("imagePreviewRow");
    const countEl = $("imageCount");
    const submitBtn = $("imageSubmitBtn");
    if (!row) return;

    row.innerHTML = "";

    gSelectedFiles.forEach((file, idx) => {
      const thumb = document.createElement("div");
      thumb.className = "image-thumb";

      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.alt = file.name;

      const removeBtn = document.createElement("button");
      removeBtn.className = "image-thumb-remove";
      removeBtn.textContent = "\u00D7";
      removeBtn.type = "button";
      removeBtn.addEventListener("click", () => {
        gSelectedFiles.splice(idx, 1);
        updateImagePreview();
      });

      thumb.appendChild(img);
      thumb.appendChild(removeBtn);
      row.appendChild(thumb);
    });

    if (countEl) {
      countEl.textContent = gSelectedFiles.length + " / 10 screenshots";
      if (gSelectedFiles.length > 0) show(countEl); else hide(countEl);
    }

    if (submitBtn) {
      if (gSelectedFiles.length >= 3) show(submitBtn); else hide(submitBtn);
    }
  }

  function handleFileSelect(files) {
    const newFiles = Array.from(files);
    const allowed = ["image/png", "image/jpeg", "image/webp", "image/heic"];

    for (const f of newFiles) {
      if (!allowed.includes(f.type) && !f.name.match(/\.(png|jpe?g|webp|heic)$/i)) continue;
      if (gSelectedFiles.length >= 10) break;
      gSelectedFiles.push(f);
    }

    updateImagePreview();
  }

  async function submitScreenshots() {
    if (gSelectedFiles.length < 3) return;

    startLoadingNarration(
      "Analyzing your screenshots...",
      [
        "Reading track names from your playlists",
        "Matching duplicate songs and artists",
        "Extracting style clues for better prompt matching",
      ],
      "Usually less than 15 seconds"
    );
    hide($("imageSubmitBtn"));

    const formData = new FormData();
    gSelectedFiles.forEach((f) => formData.append("files", f));

    try {
      const resp = await fetch("/api/chat/upload-screenshots", {
        method: "POST",
        body: formData,
      });
      const data = await resp.json();

      hideLoading();

      if (data.ok && data.tracks) {
        gExtractedTracks = data.tracks;

        addChatBubble("user", "Uploaded " + gSelectedFiles.length + " screenshots");
        gChatHistory.push({ role: "user", content: "[Uploaded " + gSelectedFiles.length + " playlist screenshots]" });

        if (data.next_question) {
          const q2 = data.next_question;
          gCurrentQuestion = q2.question_number; // 2
          updateChatProgress(gCurrentQuestion);
          gChipOptions = q2.chip_options || { artists: [], songs: [] };

          addChatBubble("assistant", q2.reply);
          gChatHistory.push({ role: "assistant", content: q2.reply });

          renderChipOptions(gChipOptions);
          showInputForType("chip_select", false);
        }
      } else {
        addChatBubble("assistant", "Hmm, I couldn't read those screenshots clearly. Try clearer images?");
        show($("imageSubmitBtn"));
        showInputForType("screenshot", false);
      }
    } catch (e) {
      hideLoading();
      console.error("Upload error:", e);
      addChatBubble("assistant", "Upload failed. Please try again.");
      show($("imageSubmitBtn"));
      showInputForType("screenshot", false);
    }
  }

  // ── Chip select (Q2) ──────────────────────────────────────────────
  function renderChipOptions(options) {
    const artistGrid = $("chipArtists");
    const songGrid = $("chipSongs");
    if (!artistGrid || !songGrid) return;

    artistGrid.innerHTML = "";
    songGrid.innerHTML = "";
    gSelectedArtists.clear();
    gSelectedSongs.clear();

    (options.artists || []).forEach((name) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = name;
      chip.addEventListener("click", () => {
        if (gSelectedArtists.has(name)) {
          gSelectedArtists.delete(name);
          chip.classList.remove("chip-selected");
        } else {
          if (gSelectedArtists.size >= 3) return;
          gSelectedArtists.add(name);
          chip.classList.add("chip-selected");
        }
        updateChipCount();
      });
      artistGrid.appendChild(chip);
    });

    (options.songs || []).forEach((song) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = song.label || song.title;
      chip.dataset.title = song.title;
      chip.dataset.artist = song.artist || "";
      chip.addEventListener("click", () => {
        const key = song.title;
        if (gSelectedSongs.has(key)) {
          gSelectedSongs.delete(key);
          chip.classList.remove("chip-selected");
        } else {
          if (gSelectedSongs.size >= 3) return;
          gSelectedSongs.add(key);
          chip.classList.add("chip-selected");
        }
        updateChipCount();
      });
      songGrid.appendChild(chip);
    });

    updateChipCount();
  }

  function updateChipCount() {
    const countEl = $("chipCount");
    const submitBtn = $("chipSubmitBtn");
    const ac = gSelectedArtists.size;
    const sc = gSelectedSongs.size;

    if (countEl) {
      countEl.textContent = ac + "/3 artists, " + sc + "/3 songs";
    }

    if (submitBtn) {
      submitBtn.disabled = (ac === 0 && sc === 0);
    }
  }

  function submitChipSelection() {
    const artists = Array.from(gSelectedArtists);
    const songs = Array.from(gSelectedSongs);

    if (artists.length === 0 && songs.length === 0) return;

    const parts = [];
    if (artists.length) parts.push("Artists: " + artists.join(", "));
    if (songs.length) parts.push("Songs: " + songs.join(", "));
    const text = parts.join(" | ");

    sendUserAnswer(text);
  }

  // ── Button select (Q4–Q9) ──────────────────────────────────────────
  function renderButtonOptions(options) {
    const container = $("buttonOptions");
    if (!container) return;
    const activeEl = document.activeElement;
    if (activeEl && typeof activeEl.blur === "function") activeEl.blur();

    container.innerHTML = "";

    options.forEach((label) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "option-btn";
      btn.textContent = label;
      btn.addEventListener("click", () => {
        btn.blur();
        sendUserAnswer(label);
      });
      container.appendChild(btn);
    });
  }

  // ── Multi-button select (Q10 personality triptych) ─────────────────
  function renderMultiButtonOptions(groups) {
    const container = $("multiButtonOptions");
    if (!container) return;
    const activeEl = document.activeElement;
    if (activeEl && typeof activeEl.blur === "function") activeEl.blur();

    container.innerHTML = "";
    const selections = {};

    groups.forEach((group) => {
      const row = document.createElement("div");
      row.className = "multi-btn-group";

      const label = document.createElement("div");
      label.className = "multi-btn-label";
      label.textContent = group.label;
      row.appendChild(label);

      const btnRow = document.createElement("div");
      btnRow.className = "multi-btn-row";

      group.options.forEach((opt) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "option-btn";
        btn.textContent = opt;
        btn.addEventListener("click", () => {
          btn.blur();
          btnRow.querySelectorAll(".option-btn").forEach((b) => b.classList.remove("option-btn-selected"));
          btn.classList.add("option-btn-selected");
          selections[group.dimension] = opt;

          if (Object.keys(selections).length === groups.length) {
            const answer = groups.map((g) => selections[g.dimension]).join(" | ");
            sendUserAnswer(answer);
          }
        });
        btnRow.appendChild(btn);
      });

      row.appendChild(btnRow);
      container.appendChild(row);
    });
  }

  // ── Profile synthesis ──────────────────────────────────────────────
  async function buildProfile() {
    startLoadingNarration(
      "Building your musical DNA...",
      [
        "Mapping your emotional triggers to sound design",
        "Prioritizing your selected favorite songs and artists",
        "Finalizing your listener personality and style blueprint",
      ],
      "Usually less than 15 seconds"
    );

    try {
      const resp = await fetch("/api/chat/build-profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: gUserId,
          history: gChatHistory,
          tracks: gExtractedTracks,
        }),
      });
      const data = await resp.json();

      hideLoading();

      if (data.ok) {
        gProfileJson = data.profile;
        gDiagnosisJson = data.diagnosis;
        gListenerProfileId = data.listener_profile_id;
        updateChatProgress(TOTAL_QUESTIONS);

        renderDiagnosis(data.profile, data.diagnosis);

        // Always allow proceeding to generation after profile build.
        show($("profileProceedBtn"));
        if (gIsAuthenticated) {
          hide($("profileCta"));
        } else {
          show($("profileCta"));
        }

        switchScreen("screenProfile");
      } else {
        addChatBubble("assistant", "Something went wrong building your profile. Let me try again...");
        console.error("Profile build error:", data.error);
      }
    } catch (e) {
      hideLoading();
      console.error("Build profile error:", e);
      addChatBubble("assistant", "Connection error while building profile. Please try again.");
    }
  }

  // ── Diagnosis rendering ────────────────────────────────────────────
  function renderDiagnosis(profile, diagnosis) {
    const typeEl = $("diagType");
    const typeNameEl = $("diagTypeName");
    const typeDescEl = $("diagTypeDesc");
    const archEl = $("diagArchetype");
    const soulEl = $("diagSoul");
    const textEl = $("diagText");
    const statsEl = $("diagStats");
    const vocalFocusEl = $("diagVocalFocus");
    const genresEl = $("diagGenres");
    const artistsEl = $("diagArtists");
    const suggestedArtistsEl = $("diagSuggestedArtists");
    const avatarImgEl = $("diagAvatarImg");
    const avatarFallbackEl = $("diagAvatarFallback");

    const listenerType = normalizeListenerTypeCode(profile, diagnosis);
    const typeMeta = getTypeMeta(listenerType);
    if (typeEl) typeEl.textContent = listenerType;
    if (typeNameEl) typeNameEl.textContent = typeMeta.name;
    if (typeDescEl) {
      typeDescEl.textContent = "";
      hide(typeDescEl);
    }

    const soulSignature = profile?.soul_signature || "";
    if (soulEl) {
      const shortSoul = formatSoulSignature(soulSignature);
      if (shortSoul) {
        soulEl.innerHTML = "";
        const soulTitle = document.createElement("div");
        soulTitle.className = "diagnosis-soul-title";
        soulTitle.textContent = "Soul Signature";

        const soulBody = document.createElement("div");
        soulBody.className = "diagnosis-soul-body";
        soulBody.textContent = shortSoul;

        soulEl.appendChild(soulTitle);
        soulEl.appendChild(soulBody);
        show(soulEl);
      } else {
        soulEl.innerHTML = "";
        hide(soulEl);
      }
    }

    if (archEl) {
      archEl.textContent = "";
      hide(archEl);
    }
    if (textEl) {
      textEl.textContent = "";
      hide(textEl);
    }

    const avatarUrl = (profile && profile.profile_avatar_url) || "";
    if (avatarImgEl && avatarFallbackEl) {
      avatarImgEl.onerror = () => {
        avatarImgEl.removeAttribute("src");
        hide(avatarImgEl);
        show(avatarFallbackEl);
      };
      if (avatarUrl) {
        avatarImgEl.src = avatarUrl;
        show(avatarImgEl);
        hide(avatarFallbackEl);
      } else {
        avatarImgEl.removeAttribute("src");
        hide(avatarImgEl);
        show(avatarFallbackEl);
      }
    }

    if (statsEl) {
      statsEl.innerHTML = "";
      const stats = [
        { label: "Emotional Depth", value: pct(profile?.emotional_profile?.emotional_depth || 0.5) },
        { label: "Curiosity", value: pct(profile?.discovery_drive || 0.5) },
        { label: "Power", value: getPowerPct(profile, listenerType) },
        { label: "Nostalgia Level", value: getNostalgiaPct(profile, listenerType) },
      ];

      stats.forEach((stat) => {
        const row = document.createElement("div");
        row.className = "diag-stat-row";

        const label = document.createElement("div");
        label.className = "diag-stat-label";
        label.textContent = stat.label;

        const track = document.createElement("div");
        track.className = "diag-stat-track";

        const fill = document.createElement("div");
        fill.className = "diag-stat-fill";
        fill.style.width = `${stat.value}%`;
        track.appendChild(fill);

        const value = document.createElement("div");
        value.className = "diag-stat-value";
        value.textContent = `${stat.value}%`;

        row.appendChild(label);
        row.appendChild(track);
        row.appendChild(value);
        statsEl.appendChild(row);
      });
    }

    if (vocalFocusEl) {
      const descriptor = getVocalFocusDescriptor(profile, listenerType);
      vocalFocusEl.innerHTML = "<strong>Vocal Focus:</strong> <span class=\"diagnosis-vocal-badge\"></span>";
      const badge = vocalFocusEl.querySelector(".diagnosis-vocal-badge");
      if (badge) badge.textContent = descriptor;
      show(vocalFocusEl);
    }

    if (genresEl && profile.dominant_genres && profile.dominant_genres.length) {
      genresEl.innerHTML = "<strong>Style Favs:</strong> " +
        profile.dominant_genres.concat(profile.subgenres || []).join(", ");
    }

    if (artistsEl && profile.identity_artists && profile.identity_artists.length) {
      artistsEl.innerHTML = "<strong>Core Artists:</strong> " +
        profile.identity_artists.join(", ");
    }

    if (suggestedArtistsEl) {
      if (profile.suggested_artists && profile.suggested_artists.length) {
        suggestedArtistsEl.innerHTML = "<strong>Suggested Artists:</strong> " +
          profile.suggested_artists.join(", ");
        show(suggestedArtistsEl);
      } else {
        suggestedArtistsEl.textContent = "";
        hide(suggestedArtistsEl);
      }
    }
  }

  // ── Radial menu setup (mood + activity) ─────────────────────────────
  function setupRadialMenu({ menuId, itemSelector, dataAttr, valueDisplayId, errorId, onSelect }) {
    const items = document.querySelectorAll(itemSelector);
    items.forEach((item) => {
      item.addEventListener("click", (e) => {
        e.preventDefault();
        const menu = $(menuId);
        if (menu) menu.classList.add("has-selection");

        items.forEach((el) => {
          el.classList.remove("is-selected");
          for (let i = 0; i < 6; i++) el.classList.remove("dist-pos-" + i);
        });

        item.classList.add("is-selected");

        let posIdx = 0;
        items.forEach((el) => {
          if (el !== item && posIdx < 6) {
            el.classList.add("dist-pos-" + posIdx);
            posIdx++;
          }
        });

        const id = item.dataset[dataAttr];
        const label = item.dataset.label || id;
        if (!id) return;

        onSelect(id, label);

        const out = $(valueDisplayId);
        if (out) out.textContent = label;

        const err = $(errorId);
        if (err) hide(err);
      });
    });
  }

  // ── Generate pipeline ──────────────────────────────────────────────
  let gGeneratingStepInterval = null;
  let gGeneratingElapsedTimer = null;
  let gGeneratingStartedAtMs = 0;

  function stopGenerationPolling() {
    if (gGenerationPollTimer) {
      clearTimeout(gGenerationPollTimer);
      gGenerationPollTimer = null;
    }
    gGenerationPollInFlight = false;
    gGenerationPollStartedAt = 0;
  }

  // ── Vibe toast messages during generation ─────────────────────────
  let gVibeToastTimer = null;
  let gVibeToastShownIdxs = [];

  const VIBE_GENERIC_MESSAGES = [
    "🎵 We’re tuning this to your brainwaves. Give us a sec",
    "🔥 Your taste is illegal in 12 countries. We’re cooking anyway",
    "🎧 You have “one more song” energy. We respect it deeply",
    "💜 Whatever today did to you, this track is about to undo it",
    "🎶 Congrats: you escaped the mainstream. We locked the door behind you",
    "✨ Mixing serotonin with a hint of menace. Stand by, bestie",
    "🧬 Your music DNA is rare. We’re handling it with gloves and pride",
    "🫶 If you cry to this, it’s not drama— it’s deluxe emotional range",
    "💿 A Spotify algorithm just felt a disturbance. That was you",
    "🎸 Consider this your personal entrée. Dessert is the drop 🍽️",
    "🌟 Your vibe made the AI sit up straight. Now it’s performing",
    "🎤 People who listen like YOU are the plot. Main character confirmed",
    "🫠 This wait is shorter than your “what should I play” spiral. Promise",
    "💫 Patience looks good on you. Great taste looks better",
    "🎵 Plot twist: the track is almost done. The drama is optional",
    "🧠 Dopamine delivery in progress. Signature required: one head nod 💅",
    "🎧 Future you is already replaying this. Present you just has to wait",
    "🌈 Stitching your vibe into bass, glitter, and a tiny bit of chaos 🎶",
    "🫡 Respect for not letting TikTok DJ your entire personality",
    "💜 drVibey diagnosis: you’re a FEELER with premium audio settings",
    "🔮 We peeked into your music soul… it’s cozy in there. Immaculate",
    "🎤 Somewhere, an AI vocalist is warming up for YOU. Iconic behavior",
    "🥹 Caring this much about music is elite behavior. Case closed",
    "🧊 Stay cool— your track is being forged like a legendary weapon",
    "🎵 If your taste was a spice, it’s the one that makes chefs nervous",
    "💃 Warning: your body may start moving without asking permission",
    "🌙 2am playlist energy? 8am commute energy? Either way, you’re served",
    "🎹 Beethoven waited years. You’re waiting seconds. We love progress",
  ];

  const VIBE_ARTIST_TEMPLATES = [
    "🔥 Liking {artist} is a green flag. Our notes are: none",
    "💜 {artist} walked so your custom track could sprint in platform boots 🏃‍♂️",
    "🎧 If {artist} heard this, they'd do that little nod. You know the one",
    "💿 Your {artist} love is basically a personality trait. We salute it 🤝",
    "🎸 {artist} fans are mysteriously cooler. Scientific? No. True? Yes",
    "✨ {artist} fans don’t listen— they *feel*. You’re one of them",
    "🫶 Thanks to {artist}, your standards are unreasonably high. We'll cope",
    "🧬 Not an obsession with {artist}… a lifestyle subscription. Respect",
    "🎤 {artist} would be proud of you for this pick. Somehow. Don’t ask",
    "🔮 Injecting a lil {artist} energy into this. Side effects: replaying 👀",
  ];

  const VIBE_GENRE_TEMPLATES = [
    "🎶 Your {genre} heart is about to be FED. Plates out",
    "🧠 Your brain on {genre}: instantly smoother. Doctors hate this trick",
    "🌊 {genre} + AI + you = a small event in the music universe 🌍",
    "💜 Another {genre} lover? Come in bestie, we kept the aux warm 🫂",
    "🔥 {genre} isn’t a genre for you— it’s home. We built you a room 👁️",
  ];

  const VIBE_ARCHETYPE_TEMPLATES = [
    "✨ A {archetype}? That’s main-character listener energy. Certified",
    "🫶 Being a {archetype} means you feel things in HD. Respectfully",
    "🔮 {archetype} detected. We’re making this extra personal on purpose",
    "💫 Only a {archetype} would vibe this hard. You’re built different 😌",
  ];

  const VIBE_CEO_MESSAGE = "💎 Add CEO on DS, he's a cutie: @angelvoize 💎";

  function buildVibeMessagePool() {
    const pool = [...VIBE_GENERIC_MESSAGES];

    const artists = gProfileJson?.identity_artists;
    if (Array.isArray(artists) && artists.length > 0) {
      for (const tpl of VIBE_ARTIST_TEMPLATES) {
        const a = artists[Math.floor(Math.random() * artists.length)];
        pool.push(tpl.replace("{artist}", a));
      }
    }

    const genres = gProfileJson?.dominant_genres;
    if (Array.isArray(genres) && genres.length > 0) {
      for (const tpl of VIBE_GENRE_TEMPLATES) {
        const g = genres[Math.floor(Math.random() * genres.length)];
        pool.push(tpl.replace("{genre}", g));
      }
    }

    const typeCode = gProfileJson?.listener_persona?.listener_mbti_like;
    const archetype = typeCode && TYPE_CATALOG[typeCode]
      ? TYPE_CATALOG[typeCode].name
      : gDiagnosisJson?.archetype;
    if (archetype) {
      for (const tpl of VIBE_ARCHETYPE_TEMPLATES) {
        pool.push(tpl.replace("{archetype}", archetype));
      }
    }

    for (let i = pool.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [pool[i], pool[j]] = [pool[j], pool[i]];
    }
    return pool;
  }

  function startVibeToasts() {
    stopVibeToasts();
    const zone = $("vibeToastZone");
    if (!zone) return;

    const pool = buildVibeMessagePool();
    let idx = 0;
    let toastCount = 0;

    function showNext() {
      if (idx >= pool.length) idx = 0;

      toastCount++;
      // ~5% chance for CEO message, but not on the first toast and not twice in a row
      const isCeoRoll = toastCount > 1 && Math.random() < 0.05;
      const msg = isCeoRoll ? VIBE_CEO_MESSAGE : pool[idx++];

      const prev = zone.querySelector(".vibe-toast");
      if (prev) {
        prev.classList.add("vibe-toast-out");
        setTimeout(() => prev.remove(), 350);
      }

      setTimeout(() => {
        const el = document.createElement("div");
        el.className = "vibe-toast";
        el.textContent = msg;
        zone.appendChild(el);
      }, prev ? 380 : 0);

      gVibeToastTimer = setTimeout(showNext, 5000);
    }

    gVibeToastTimer = setTimeout(showNext, 3000);
  }

  function stopVibeToasts() {
    if (gVibeToastTimer) {
      clearTimeout(gVibeToastTimer);
      gVibeToastTimer = null;
    }
    const zone = $("vibeToastZone");
    if (zone) zone.innerHTML = "";
  }

  function startGeneratingScreen() {
    if (!$("screenGenerating")) {
      showLoading("Generating your track…");
      return;
    }
    const steps = [
      "Reading your musical DNA…",
      "Picking the right vibe…",
      "Composing your track…",
      "Rendering audio…",
      "Almost there…",
    ];
    const stepEl = $("generatingStep");
    const contextEl = $("generatingContext");
    const elapsedEl = $("generatingElapsed");
    if (contextEl) {
      const parts = [];
      if (gSelectedMoodLabel) parts.push(gSelectedMoodLabel);
      if (gSelectedActivityLabel) parts.push(gSelectedActivityLabel);
      contextEl.textContent = parts.length ? parts.join(" · ") : "";
      contextEl.classList.toggle("is-hidden", parts.length === 0);
    }
    let i = 0;
    if (stepEl) stepEl.textContent = steps[0];
    gGeneratingStartedAtMs = Date.now();
    const renderElapsed = () => {
      if (!elapsedEl || !gGeneratingStartedAtMs) return;
      const elapsedSec = Math.max(0, Math.floor((Date.now() - gGeneratingStartedAtMs) / 1000));
      elapsedEl.textContent = `${elapsedSec}s elapsed`;
    };
    renderElapsed();
    if (gGeneratingElapsedTimer) clearInterval(gGeneratingElapsedTimer);
    gGeneratingElapsedTimer = setInterval(renderElapsed, 1000);
    if (gGeneratingStepInterval) clearInterval(gGeneratingStepInterval);
    gGeneratingStepInterval = setInterval(() => {
      i = (i + 1) % steps.length;
      if (stepEl) stepEl.textContent = steps[i];
    }, 6000);
    switchScreen("screenGenerating");
    startVibeToasts();
  }

  function stopGeneratingScreen() {
    stopGenerationPolling();
    stopVibeToasts();
    if (gGeneratingStepInterval) {
      clearInterval(gGeneratingStepInterval);
      gGeneratingStepInterval = null;
    }
    if (gGeneratingElapsedTimer) {
      clearInterval(gGeneratingElapsedTimer);
      gGeneratingElapsedTimer = null;
    }
    gGeneratingStartedAtMs = 0;
    const elapsedEl = $("generatingElapsed");
    if (elapsedEl) elapsedEl.textContent = "";
    hideLoading();
  }

  async function handleGenerate() {
    try {
      startGeneratingScreen();

      const resp = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: gUserId,
          mood: gSelectedMoodId,
          mood_intensity: gMoodIntensity,
          activity: gSelectedActivityId,
          instrumental: gInstrumental,
          song_reference: gSongReference,
          genre: gGenre,
          bpm: gBpm,
          language: gLanguage,
          surprise_me: gSurpriseMe,
        }),
      });

      const data = await resp.json();

      if (!resp.ok && resp.status === 403) {
        stopGeneratingScreen();
        // User needs to sign in first
        const confirmed = confirm("You need to sign in to generate music. Sign in now?");
        if (confirmed) {
          const success = await signInWithGoogle("generate");
          if (success) {
            // Retry generation
            handleGenerate();
          }
        }
        return;
      }

      if (data.ok && data.generation_id) {
        pollGeneration(data.generation_id);
      } else {
        throw new Error(data.error || "Generation failed to start");
      }
    } catch (e) {
      stopGeneratingScreen();
      switchScreen("screenExtras");
      console.error(e);
      alert("Failed to start generation: " + e.message);
    }
  }

  function pollGeneration(genId) {
    stopGenerationPolling();
    gGenerationPollStartedAt = Date.now();

    function nextPollDelayMs() {
      const elapsedMs = Date.now() - gGenerationPollStartedAt;
      if (elapsedMs < 30000) return 1000;
      if (elapsedMs < 120000) return 2000;
      return 3000;
    }

    function scheduleNextPoll(delayMs) {
      gGenerationPollTimer = setTimeout(runPoll, delayMs ?? nextPollDelayMs());
    }

    async function runPoll() {
      if (gGenerationPollInFlight) {
        scheduleNextPoll(250);
        return;
      }

      gGenerationPollInFlight = true;
      try {
        const resp = await fetch("/api/generation/" + genId);
        const data = await resp.json();

        if (!data.ok) {
          scheduleNextPoll();
          return;
        }

        if (data.status === "succeeded") {
          stopGeneratingScreen();
          gCurrentGenId = genId;
          gCurrentGenIsFavourite = false;
          gCurrentGenLikeStatus = null;
          gCurrentGenCreatedAt = new Date().toISOString();
          updateFavButton();
          updateLikeButtons();
          renderResult(data.result);
          startPlaybackTimer(gCurrentGenCreatedAt);
          renderSimilarSongs(data.result);
          switchScreen("screenResult");
        } else if (data.status === "failed") {
          stopGeneratingScreen();
          switchScreen("screenMood");
          alert("Generation failed: " + (data.error || "Unknown error"));
        } else {
          scheduleNextPoll();
        }
      } catch (e) {
        console.error("Polling error", e);
        scheduleNextPoll();
      } finally {
        gGenerationPollInFlight = false;
      }
    }

    runPoll();
  }

  function renderResult(resultJson) {
    let tracks = null;
    if (resultJson?.record_info?.data?.response?.sunoData) {
      tracks = resultJson.record_info.data.response.sunoData;
    } else if (resultJson?.record_info?.data?.response?.data) {
      tracks = resultJson.record_info.data.response.data;
    } else if (resultJson?.final) {
      tracks = resultJson.final;
    } else if (resultJson?.data?.response?.sunoData) {
      tracks = resultJson.data.response.sunoData;
    } else if (resultJson?.data?.data) {
      tracks = resultJson.data.data;
    }

    const song = Array.isArray(tracks) && tracks.length > 0 ? tracks[0] : null;
    if (!song) {
      console.error("No song found in result:", resultJson);
      alert("No song data returned.");
      return;
    }

    const moodLabel = $("resultMood");
    const audioEl = $("audioEl");
    const coverImg = $("coverImg");
    const resultTitle = document.querySelector(".result-title");

    if (resultTitle && song.title) resultTitle.textContent = song.title;
    if (moodLabel) moodLabel.textContent = "Mood: " + (gSelectedMoodLabel || gSelectedMoodId || "\u2014");

    const streamUrl = song.streamAudioUrl || song.sourceStreamAudioUrl ||
      song.stream_audio_url || song.source_stream_audio_url || "";
    const playbackUrl = streamUrl;

    if (audioEl && playbackUrl) {
      audioEl.src = playbackUrl;
      audioEl.load();
    } else if (audioEl) {
      audioEl.pause();
      audioEl.removeAttribute("src");
      audioEl.load();
    }

    const imageUrl = song.imageUrl || song.sourceImageUrl || song.image_url || song.source_image_url || "";
    if (coverImg && imageUrl) coverImg.src = imageUrl;

    setupPlayerListeners({ hasFinalAudio: false, canPlay: !!playbackUrl });
  }

  function setupPlayerListeners(options) {
    const audioEl = $("audioEl");
    const playPauseBtn = $("playPauseBtn");
    const seek = $("seek");

    if (!audioEl) return;
    const hasFinalAudio = !!(options && options.hasFinalAudio);
    const canPlay = !!(options && options.canPlay);
    gPlayerHasFinalAudio = hasFinalAudio;
    gPlayerPlaybackEnabled = canPlay;

    if (!audioEl.dataset.playerBound) {
      audioEl.addEventListener("timeupdate", refreshPlayerTimeline);
      audioEl.addEventListener("loadedmetadata", refreshPlayerTimeline);

      // When the browser learns the full duration, the audio is fully available
      audioEl.addEventListener("durationchange", () => {
        if (isFinite(audioEl.duration) && audioEl.duration > 0) {
          gPlayerHasFinalAudio = true;
        }
        refreshPlayerTimeline();
      });

      audioEl.addEventListener("ended", () => {
        const btn = $("playPauseBtn");
        if (btn) btn.textContent = "\u25B6";
        gPlayerHasFinalAudio = true;
        // Full reload so mobile browsers can replay stream URLs
        const src = audioEl.getAttribute("src") || audioEl.src;
        if (src && src !== "#") {
          audioEl.src = src;
          audioEl.load();
        }
        refreshPlayerTimeline();
      });

      // Handle stream dropping (Suno stream can break when generation finalises)
      audioEl.addEventListener("error", () => {
        if (!gPlayerPlaybackEnabled) return;
        const src = audioEl.getAttribute("src") || audioEl.src;
        if (!src || src === "" || src === "#") return;
        console.warn("Audio error — attempting reload of:", src);
        const lastTime = audioEl.currentTime || 0;
        audioEl.src = src;
        audioEl.load();
        audioEl.addEventListener("loadedmetadata", function _seekBack() {
          audioEl.removeEventListener("loadedmetadata", _seekBack);
          try {
            if (lastTime > 0 && isFinite(audioEl.duration) && lastTime < audioEl.duration) {
              audioEl.currentTime = lastTime;
            }
          } catch (_) { /* ignore */ }
          gPlayerHasFinalAudio = true;
          refreshPlayerTimeline();
        }, { once: true });
      });

      audioEl.dataset.playerBound = "1";
    }

    if (playPauseBtn) {
      playPauseBtn.disabled = !gPlayerPlaybackEnabled;
      playPauseBtn.classList.toggle("is-disabled", !gPlayerPlaybackEnabled);
      playPauseBtn.setAttribute("aria-disabled", gPlayerPlaybackEnabled ? "false" : "true");
      if (!gPlayerPlaybackEnabled) playPauseBtn.textContent = "\u25B6";
      playPauseBtn.onclick = async () => {
        if (!gPlayerPlaybackEnabled) return;
        if (audioEl.paused) {
          // Helper: reload source and play once ready (needed on mobile)
          function reloadAndPlay() {
            const src = audioEl.getAttribute("src") || audioEl.src;
            if (!src || src === "#") return;
            audioEl.src = src;
            audioEl.load();
            audioEl.addEventListener("canplay", function _onReady() {
              audioEl.removeEventListener("canplay", _onReady);
              gPlayerHasFinalAudio = true;
              audioEl.play()
                .then(() => { playPauseBtn.textContent = "\u275A\u275A"; })
                .catch((err) => { console.error("Reload play failed", err); });
              refreshPlayerTimeline();
            }, { once: true });
          }

          // If the audio ended or is in an error state, reload first
          if (audioEl.ended || audioEl.error) {
            reloadAndPlay();
            return;
          }

          try {
            await audioEl.play();
            playPauseBtn.textContent = "\u275A\u275A";
          } catch (e) {
            console.error("Play error — reloading source for mobile", e);
            reloadAndPlay();
          }
        } else {
          audioEl.pause();
          playPauseBtn.textContent = "\u25B6";
        }
      };
    }

    if (seek) {
      seek.oninput = (e) => {
        if (!gPlayerPlaybackEnabled) return;
        if (!isFinite(audioEl.duration) || audioEl.duration <= 0) return;

        let pct = Number(e.target.value || 0);
        if (!gPlayerHasFinalAudio) {
          const currentPct = (audioEl.currentTime / audioEl.duration) * 100;
          pct = Math.min(pct, currentPct);
          e.target.value = String(pct);
        }

        audioEl.currentTime = (pct / 100) * audioEl.duration;
      };
    }

    refreshPlayerTimeline();
  }

  // ==================================================================
  // INIT
  // ==================================================================
  document.addEventListener("DOMContentLoaded", async () => {
    gUserId = document.body.dataset.userId;
    renderTypeCatalog();

    // ── Determine initial screen (robust, never shows blank) ──
    let initialScreenDone = false;

    // Helper: go to the right screen after a successful auth
    function _authLanded(dest) {
      if (dest === "result") {
        let genId = null;
        try { genId = sessionStorage.getItem("_auth_return_gen"); sessionStorage.removeItem("_auth_return_gen"); } catch (e) {}
        if (genId) {
          loadGenerationById(genId);
          return;
        }
      }
      if (dest === "mood") {
        switchScreen("screenMood");
        setTimeout(() => autoSelectMood(), 100);
      } else if (dest === "generate") {
        loadSavedProfile();
        loadRecentGenerations();
        switchScreen("screenHome");
      } else {
        // "home" or default
        loadSavedProfile();
        loadRecentGenerations();
        switchScreen("screenHome");
      }
    }

    // 1) Check server session auth (handles Google OAuth callback return)
    if (!initialScreenDone) {
      try {
        const isAuth = await checkAuthState();
        if (isAuth) {
          let dest = "home";
          try {
            dest = sessionStorage.getItem("_auth_dest") || "home";
            sessionStorage.removeItem("_auth_dest");
          } catch (e) { /* private mode */ }
          _authLanded(dest);
          initialScreenDone = true;
        }
      } catch (e) {
        console.error("Auth state check error (non-fatal):", e);
      }
    }

    // 2) Anonymous fallback -> straight to chat
    if (!initialScreenDone) {
      switchScreen("screenChat");
      initChat();
    }

    // ── Header button handlers ──
    // ── Logo button handler ──
    const headerLogoBtn = $("headerLogoBtn");
    if (headerLogoBtn) {
      headerLogoBtn.addEventListener("click", () => {
        if (gIsAuthenticated) {
          loadRecentGenerations();
          switchScreen("screenHome");
        } else {
          openAuthScreen("screenLogin");
        }
      });
    }

    const headerUserBtn = $("headerUserBtn");
    if (headerUserBtn) {
      headerUserBtn.addEventListener("click", () => {
        if (gIsAuthenticated) {
          toggleDropdown();
        } else {
          openAuthScreen("screenLogin");
        }
      });
    }

    const headerMyVibeBtn = $("headerMyVibeBtn");
    if (headerMyVibeBtn) {
      const openMyVibeFromHeader = () => {
        if (gIsAuthenticated) {
          showMyVibe();
        } else {
          openAuthScreen("screenLogin");
        }
      };
      headerMyVibeBtn.addEventListener("click", openMyVibeFromHeader);
      headerMyVibeBtn.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openMyVibeFromHeader();
        }
      });
    }

    // Close side menu via backdrop click
    const sideMenuBackdrop = $("sideMenuBackdrop");
    if (sideMenuBackdrop) {
      sideMenuBackdrop.addEventListener("click", closeSideMenu);
    }

    // Close side menu via close button
    const sideMenuCloseBtn = $("sideMenuClose");
    if (sideMenuCloseBtn) {
      sideMenuCloseBtn.addEventListener("click", closeSideMenu);
    }

    // ── Side menu item handlers ──
    document.querySelectorAll(".side-menu-item[data-action]").forEach((item) => {
      item.addEventListener("click", () => {
        handleDropdownAction(item.dataset.action);
      });
    });

    // ── Login screen Google handler ──
    const loginGoogle = $("loginGoogle");
    if (loginGoogle) {
      loginGoogle.addEventListener("click", async () => {
        let dest = "home";
        try { if (sessionStorage.getItem("_auth_return_gen")) dest = "result"; } catch (e) {}
        const success = await signInWithGoogle(dest);
        if (success) {
          loadRecentGenerations();
          switchScreen("screenHome");
        }
      });
    }

    // ── Register screen Google handler ──
    const registerGoogle = $("registerGoogle");
    if (registerGoogle) {
      registerGoogle.addEventListener("click", async () => {
        let dest = "home";
        try { if (sessionStorage.getItem("_auth_return_gen")) dest = "result"; } catch (e) {}
        const success = await signInWithGoogle(dest);
        if (success) {
          loadRecentGenerations();
          switchScreen("screenHome");
        }
      });
    }

    // ── Login email form handler ──
    const loginEmailForm = $("loginEmailForm");
    if (loginEmailForm) {
      loginEmailForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        clearAuthMsgs("loginEmail");

        const email = ($("loginEmailInput").value || "").trim();
        const password = $("loginPasswordInput").value || "";

        if (!email) { showAuthMsg("loginEmailError", "Please enter your email address."); return; }
        if (!password) { showAuthMsg("loginEmailError", "Please enter your password."); return; }

        const authenticated = await loginWithEmail(email, password);
        if (authenticated) {
          let dest = "home";
          try {
            if (sessionStorage.getItem("_auth_return_gen")) dest = "result";
            else dest = sessionStorage.getItem("_auth_dest") || "home";
            sessionStorage.removeItem("_auth_dest");
          } catch (err) {}
          _authLanded(dest);
        }
      });
    }

    // ── Register email form handler ──
    const registerEmailForm = $("registerEmailForm");
    if (registerEmailForm) {
      registerEmailForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        clearAuthMsgs("registerEmail");

        const email = ($("registerEmailInput").value || "").trim();
        const password = $("registerPasswordInput").value || "";
        const confirmPassword = $("registerConfirmPasswordInput").value || "";

        if (!email) { showAuthMsg("registerEmailError", "Please enter your email address."); return; }
        if (!isValidPassword(password)) { showAuthMsg("registerEmailError", "Password must be at least 7 characters and include at least 1 number."); return; }
        if (password !== confirmPassword) { showAuthMsg("registerEmailError", "Passwords do not match."); return; }

        const authenticated = await registerWithEmail(email, password);
        if (authenticated) {
          let dest = "home";
          try {
            if (sessionStorage.getItem("_auth_return_gen")) dest = "result";
            else dest = sessionStorage.getItem("_auth_dest") || "home";
            sessionStorage.removeItem("_auth_dest");
          } catch (err) {}
          _authLanded(dest);
        }
      });
    }

    // ── Switch between login / register screens ──
    const goToRegister = $("goToRegister");
    if (goToRegister) {
      goToRegister.addEventListener("click", () => switchScreen("screenRegister"));
    }
    const goToLogin = $("goToLogin");
    if (goToLogin) {
      goToLogin.addEventListener("click", () => switchScreen("screenLogin"));
    }

    // ── Back buttons on auth screens ──
    const loginBack = $("loginBack");
    if (loginBack) {
      loginBack.addEventListener("click", () => navigateBackFromAuth());
    }
    const registerBack = $("registerBack");
    if (registerBack) {
      registerBack.addEventListener("click", () => navigateBackFromAuth());
    }

    // ── Chat text input handlers ──
    const chatInput = $("chatInput");
    const chatSendBtn = $("chatSendBtn");

    if (chatSendBtn) {
      chatSendBtn.addEventListener("click", () => {
        const text = (chatInput.value || "").trim();
        if (text) sendUserAnswer(text);
      });
    }

    if (chatInput) {
      chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          const text = (chatInput.value || "").trim();
          if (text) sendUserAnswer(text);
        }
      });

      chatInput.addEventListener("input", () => {
        chatInput.style.height = "auto";
        chatInput.style.height = Math.min(chatInput.scrollHeight, 100) + "px";
      });
    }

    // ── Skip button handler ──
    const skipBtn = $("skipBtn");
    if (skipBtn) {
      skipBtn.addEventListener("click", () => {
        if (gCurrentSkippable) sendUserAnswer(SKIP_TOKEN);
      });
    }

    // ── Chip submit handler (Q2) ──
    const chipSubmitBtn = $("chipSubmitBtn");
    if (chipSubmitBtn) {
      chipSubmitBtn.addEventListener("click", () => submitChipSelection());
    }

    // ── Image upload handlers ──
    const fileInput = $("fileInput");
    const dropZone = $("dropZone");
    const imageSubmitBtn = $("imageSubmitBtn");

    if (dropZone) {
      dropZone.addEventListener("click", () => { if (fileInput) fileInput.click(); });

      dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropZone.classList.add("drag-over");
      });

      dropZone.addEventListener("dragleave", () => {
        dropZone.classList.remove("drag-over");
      });

      dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropZone.classList.remove("drag-over");
        if (e.dataTransfer.files.length) handleFileSelect(e.dataTransfer.files);
      });
    }

    if (fileInput) {
      fileInput.addEventListener("change", () => {
        if (fileInput.files.length) handleFileSelect(fileInput.files);
        fileInput.value = "";
      });
    }

    if (imageSubmitBtn) {
      imageSubmitBtn.addEventListener("click", submitScreenshots);
    }

    // ── Profile screen handlers ──
    const diagTypeModal = $("diagTypeModal");
    if (diagTypeModal && diagTypeModal.parentElement !== document.body) {
      document.body.appendChild(diagTypeModal);
    }

    const diagTypeInfoBtn = $("diagTypeInfoBtn");
    if (diagTypeInfoBtn) {
      diagTypeInfoBtn.addEventListener("click", openTypeModal);
    }

    const diagTypeModalClose = $("diagTypeModalClose");
    if (diagTypeModalClose) {
      diagTypeModalClose.addEventListener("click", closeTypeModal);
    }

    const diagTypeModalBackdrop = $("diagTypeModalBackdrop");
    if (diagTypeModalBackdrop) {
      diagTypeModalBackdrop.addEventListener("click", closeTypeModal);
    }

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeTypeModal();
    });

    const diagShareBtn = $("diagShareBtn");
    if (diagShareBtn) {
      diagShareBtn.addEventListener("click", () => {
        shareProfile();
      });
    }

    // Profile screen signup buttons -> Firebase auth
    const signupGoogle = $("signupGoogle");
    if (signupGoogle) {
      signupGoogle.addEventListener("click", async () => {
        const success = await signInWithGoogle("mood");
        if (success) {
          switchScreen("screenMood");
          setTimeout(() => autoSelectMood(), 100);
        }
      });
    }

    // Profile screen email signup -> go to register screen
    const signupEmail = $("signupEmail");
    if (signupEmail) {
      signupEmail.addEventListener("click", () => {
        openAuthScreen("screenRegister");
      });
    }

    // Profile proceed button (for already-authenticated users)
    const profileProceedBtn = $("profileProceedBtn");
    if (profileProceedBtn) {
      profileProceedBtn.addEventListener("click", () => {
        switchScreen("screenMood");
        setTimeout(() => autoSelectMood(), 100);
      });
    }

    // ── Result screen: Back to Home ──
    const resultHomeBtn = $("resultHomeBtn");
    if (resultHomeBtn) {
      resultHomeBtn.addEventListener("click", () => {
        if (gIsAuthenticated) {
          loadRecentGenerations();
          switchScreen("screenHome");
        } else {
          window.location.reload();
        }
      });
    }

    // ── Result screen: Favourite + Like/Dislike buttons ──
    const favBtn = $("favBtn");
    if (favBtn) {
      favBtn.addEventListener("click", toggleFavourite);
    }
    const likeBtn = $("likeBtn");
    if (likeBtn) {
      likeBtn.addEventListener("click", () => toggleLike("liked"));
    }
    const dislikeBtn = $("dislikeBtn");
    if (dislikeBtn) {
      dislikeBtn.addEventListener("click", () => toggleLike("disliked"));
    }

    // ── Mood screen (existing) ──
    setupRadialMenu({
      menuId: "moodMenu",
      itemSelector: "#moodMenu .menu-item",
      dataAttr: "mood",
      valueDisplayId: "selectedMoodValue",
      errorId: "moodError",
      onSelect: (id, label) => { gSelectedMoodId = id; gSelectedMoodLabel = label; },
    });

    const intensitySlider = $("intensitySlider");
    if (intensitySlider) {
      intensitySlider.addEventListener("input", () => {
        gMoodIntensity = parseInt(intensitySlider.value, 10) / 100;
      });
    }

    const moodNextBtn = $("moodNextBtn");
    const moodBackBtn = $("moodBackBtn");
    if (moodBackBtn) {
      moodBackBtn.addEventListener("click", () => {
        // Go back to whichever profile screen was active
        if (gProfileJson && gProfileJson.profile_type === "psychoacoustic") {
          switchScreen("screenPsychProfile");
        } else {
          switchScreen("screenProfile");
        }
      });
    }
    if (moodNextBtn) {
      moodNextBtn.addEventListener("click", () => {
        if (!gSelectedMoodId) { const err = $("moodError"); if (err) show(err); return; }
        const err = $("moodError"); if (err) hide(err);
        switchScreen("screenActivity");
        setTimeout(() => autoSelectActivity(), 100);
      });
    }

    // ── Activity screen (existing) ──
    setupRadialMenu({
      menuId: "activityMenu",
      itemSelector: "#activityMenu .menu-item",
      dataAttr: "activity",
      valueDisplayId: "selectedActivityValue",
      errorId: "activityError",
      onSelect: (id, label) => { gSelectedActivityId = id; gSelectedActivityLabel = label; },
    });

    const btnInst = $("toggleInstrumental");
    const btnVocal = $("toggleVocal");
    if (btnInst && btnVocal) {
      btnInst.addEventListener("click", () => {
        gInstrumental = true;
        btnInst.classList.add("is-active");
        btnVocal.classList.remove("is-active");
      });
      btnVocal.addEventListener("click", () => {
        gInstrumental = false;
        btnVocal.classList.add("is-active");
        btnInst.classList.remove("is-active");
      });
    }

    const actNextBtn = $("activityNextBtn");
    const activityBackBtn = $("activityBackBtn");
    if (activityBackBtn) {
      activityBackBtn.addEventListener("click", () => {
        switchScreen("screenMood");
      });
    }
    if (actNextBtn) {
      actNextBtn.addEventListener("click", () => {
        if (!gSelectedActivityId) { const err = $("activityError"); if (err) show(err); return; }
        const err = $("activityError"); if (err) hide(err);
        switchScreen("screenExtras");
      });
    }

    // ── Extras screen (existing) ──
    const songRefInput = $("songRefInput");
    const genreSelect = $("genreSelect");
    const languageSelect = $("languageSelect");
    const bpmSlider = $("bpmSlider");
    const surpriseToggle = $("surpriseToggle");
    const bpmValue = $("bpmValue");
    const extrasNextBtn = $("extrasNextBtn");
    const extrasBackBtn = $("extrasBackBtn");

    if (extrasBackBtn) {
      extrasBackBtn.addEventListener("click", () => {
        switchScreen("screenActivity");
      });
    }

    function updateExtrasButton() {
      const hasSong = songRefInput && songRefInput.value.trim() !== "";
      const hasGenre = genreSelect && genreSelect.value !== "";
      const hasLanguageOverride = languageSelect && (languageSelect.value || "english") !== "english";
      const hasBpm = bpmSlider && parseInt(bpmSlider.value, 10) > 0;
      const hasSurprise = !!(surpriseToggle && surpriseToggle.checked);
      if (extrasNextBtn) {
        extrasNextBtn.textContent = (hasSong || hasGenre || hasLanguageOverride || hasBpm || hasSurprise)
          ? "NEXT >"
          : "SKIP >";
      }
    }

    if (songRefInput) {
      songRefInput.addEventListener("input", () => {
        gSongReference = songRefInput.value.trim() || null;
        updateExtrasButton();
      });
    }
    if (genreSelect) {
      genreSelect.addEventListener("change", () => {
        gGenre = genreSelect.value || null;
        updateExtrasButton();
      });
    }
    if (languageSelect) {
      gLanguage = (languageSelect.value || "english").toLowerCase();
      languageSelect.addEventListener("change", () => {
        gLanguage = (languageSelect.value || "english").toLowerCase();
        updateExtrasButton();
      });
    }
    if (bpmSlider) {
      bpmSlider.value = "0";
      bpmSlider.addEventListener("input", () => {
        const v = parseInt(bpmSlider.value, 10);
        if (v >= 60) { gBpm = v; if (bpmValue) bpmValue.textContent = v; }
        else { gBpm = null; if (bpmValue) bpmValue.textContent = "--"; }
        updateExtrasButton();
      });
    }
    if (surpriseToggle) {
      gSurpriseMe = !!surpriseToggle.checked;
      surpriseToggle.addEventListener("change", () => {
        gSurpriseMe = !!surpriseToggle.checked;
        updateExtrasButton();
      });
    }

    if (extrasNextBtn) {
      extrasNextBtn.addEventListener("click", () => handleGenerate());
    }

    updateExtrasButton();

    // ── Path choice handlers (Quick Analysis vs Full Test) ──
    const pathQuickBtn = $("pathQuickBtn");
    if (pathQuickBtn) {
      pathQuickBtn.addEventListener("click", startQuickAnalysis);
    }
    const pathFullTestBtn = $("pathFullTestBtn");
    if (pathFullTestBtn) {
      pathFullTestBtn.addEventListener("click", startFullTest);
    }

    // ── Psychoacoustic profile screen handlers ──
    const psychGenerateBtn = $("psychGenerateBtn");
    if (psychGenerateBtn) {
      psychGenerateBtn.addEventListener("click", () => {
        switchScreen("screenMood");
        setTimeout(() => autoSelectMood(), 100);
      });
    }

    const psychShareBtn = $("psychShareBtn");
    if (psychShareBtn) {
      psychShareBtn.addEventListener("click", () => {
        shareProfile();
      });
    }

    // Psych profile signup buttons
    const psychSignupGoogle = $("psychSignupGoogle");
    if (psychSignupGoogle) {
      psychSignupGoogle.addEventListener("click", async () => {
        const success = await signInWithGoogle("mood");
        if (success) {
          switchScreen("screenMood");
          setTimeout(() => autoSelectMood(), 100);
        }
      });
    }
    const psychSignupEmail = $("psychSignupEmail");
    if (psychSignupEmail) {
      psychSignupEmail.addEventListener("click", () => {
        openAuthScreen("screenRegister");
      });
    }
  });

  // ── Auto-select helpers (existing mood/activity) ───────────────────
  function autoSelectMood() {
    const menuItems = document.querySelectorAll("#moodMenu .menu-item");
    let romanticItem = null;
    menuItems.forEach((item) => { if (item.dataset.mood === "romantic") romanticItem = item; });

    if (romanticItem) {
      const menu = $("moodMenu");
      if (menu) menu.classList.add("has-selection");
      menuItems.forEach((el) => {
        el.classList.remove("is-selected");
        for (let i = 0; i < 6; i++) el.classList.remove("dist-pos-" + i);
      });
      romanticItem.classList.add("is-selected");
      let posIdx = 0;
      menuItems.forEach((el) => {
        if (el !== romanticItem && posIdx < 6) { el.classList.add("dist-pos-" + posIdx); posIdx++; }
      });
      gSelectedMoodId = "romantic";
      gSelectedMoodLabel = romanticItem.dataset.label || "Romantic";
      const out = $("selectedMoodValue");
      if (out) out.textContent = gSelectedMoodLabel;
      const menuOpen = $("menu-open");
      if (menuOpen) menuOpen.checked = true;
    }
  }

  function autoSelectActivity() {
    const menuItems = document.querySelectorAll("#activityMenu .menu-item");
    let partyingItem = null;
    menuItems.forEach((item) => { if (item.dataset.activity === "partying") partyingItem = item; });

    if (partyingItem) {
      const menu = $("activityMenu");
      if (menu) menu.classList.add("has-selection");
      menuItems.forEach((el) => {
        el.classList.remove("is-selected");
        for (let i = 0; i < 6; i++) el.classList.remove("dist-pos-" + i);
      });
      partyingItem.classList.add("is-selected");
      let posIdx = 0;
      menuItems.forEach((el) => {
        if (el !== partyingItem && posIdx < 6) { el.classList.add("dist-pos-" + posIdx); posIdx++; }
      });
      gSelectedActivityId = "partying";
      gSelectedActivityLabel = partyingItem.dataset.label || "Partying";
      const out = $("selectedActivityValue");
      if (out) out.textContent = gSelectedActivityLabel;
      const menuOpen = $("activity-menu-open");
      if (menuOpen) menuOpen.checked = true;
    }
  }
})();
