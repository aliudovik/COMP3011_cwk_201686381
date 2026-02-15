/* app/static/public.js — drVibey Chat + Auth + Generation Flow */

(function () {
  const $ = (id) => document.getElementById(id);

  // ── State ──────────────────────────────────────────────────────────
  let gUserId = null;
  let gIsAuthenticated = false;
  let gCurrentUser = null; // { id, email, display_name, photo_url, auth_provider }

  // Chat state
  let gChatHistory = [];           // [{role: "assistant"|"user", content: "..."}]
  let gCurrentQuestion = 0;        // which question was last ASKED (1-10)
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

  // Current result state (for favourite toggling)
  let gCurrentGenId = null;
  let gCurrentGenIsFavourite = false;
  let gDownloadPollTimer = null; // interval ID for download URL polling

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

  const SKIP_TOKEN = "[skipped]";
  const TOTAL_QUESTIONS = 10;

  // ── Helpers ────────────────────────────────────────────────────────
  function show(el) { if (el) el.classList.remove("is-hidden"); }
  function hide(el) { if (el) el.classList.add("is-hidden"); }

  function showLoading(text) {
    const overlay = $("loadingOverlay");
    const t = $("loadingText");
    if (t) t.textContent = text || "Loading\u2026";
    if (overlay) { show(overlay); overlay.setAttribute("aria-hidden", "false"); }
  }

  function hideLoading() {
    const overlay = $("loadingOverlay");
    if (overlay) { hide(overlay); overlay.setAttribute("aria-hidden", "true"); }
  }

  function msToTime(secs) {
    if (!isFinite(secs)) return "00:00";
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function switchScreen(screenId) {
    const screens = [
      "screenLogin", "screenHome", "screenFavourites", "screenChat", "screenProfile",
      "screenMood", "screenActivity", "screenExtras", "screenResult",
    ];
    screens.forEach((id) => {
      const el = $(id);
      if (el) { if (id === screenId) show(el); else hide(el); }
    });
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

    hide($("chatInputWrap"));
    hide($("imageUploadWrap"));
    hide($("chipSelectWrap"));
    hide($("buttonSelectWrap"));
    hide($("skipWrap"));

    switch (inputType) {
      case "screenshot":
        show($("imageUploadWrap"));
        break;
      case "chip_select":
        show($("chipSelectWrap"));
        break;
      case "text":
        show($("chatInputWrap"));
        $("chatInput").focus();
        if (skippable) show($("skipWrap"));
        break;
      case "buttons":
        show($("buttonSelectWrap"));
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

  async function signInWithGoogle() {
    if (typeof firebase === "undefined" || !firebase.auth) {
      alert("Firebase is not configured yet. Please check back later.");
      return false;
    }

    try {
      const provider = new firebase.auth.GoogleAuthProvider();
      const result = await firebase.auth().signInWithPopup(provider);
      const idToken = await result.user.getIdToken();

      showLoading("Signing in...");
      const resp = await fetch("/api/auth/verify-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id_token: idToken }),
      });
      const data = await resp.json();
      hideLoading();

      if (data.ok && data.user) {
        gIsAuthenticated = true;
        gCurrentUser = data.user;
        gUserId = data.user.id;
        updateHeaderUI();
        return true;
      } else {
        alert("Sign-in failed: " + (data.error || "Unknown error"));
        return false;
      }
    } catch (e) {
      hideLoading();
      console.error("Google sign-in error:", e);
      if (e.code !== "auth/popup-closed-by-user") {
        alert("Sign-in error: " + e.message);
      }
      return false;
    }
  }

  /**
   * Send a passwordless sign-in link to the user's email via Firebase.
   * @param {string} email
   * @returns {Promise<boolean>} true if the email was sent successfully
   */
  async function sendEmailSignInLink(email) {
    if (typeof firebase === "undefined" || !firebase.auth) {
      alert("Firebase is not configured yet. Please check back later.");
      return false;
    }

    // Build the redirect URL: back to our app root so completeEmailSignIn() fires
    const redirectUrl = window.location.origin + "/";

    const actionCodeSettings = {
      url: redirectUrl,
      handleCodeInApp: true,
    };

    try {
      await firebase.auth().sendSignInLinkToEmail(email, actionCodeSettings);
      // Save the email in localStorage so we can complete sign-in when they click the link
      window.localStorage.setItem("emailForSignIn", email);
      return true;
    } catch (e) {
      console.error("Email link send error:", e);
      const msg = {
        "auth/invalid-email": "Please enter a valid email address.",
        "auth/too-many-requests": "Too many attempts. Please wait a moment and try again.",
        "auth/missing-continue-uri": "Configuration error. Please contact support.",
        "auth/unauthorized-continue-uri": "This domain is not authorized. Please contact support.",
      }[e.code] || e.message;
      showEmailError(msg);
      return false;
    }
  }

  /**
   * Check if the current page URL is an email sign-in link and complete the flow.
   * Called on page load.
   * @returns {Promise<boolean>} true if sign-in was completed
   */
  async function completeEmailSignIn() {
    if (typeof firebase === "undefined" || !firebase.auth) return false;

    if (!firebase.auth().isSignInWithEmailLink(window.location.href)) {
      return false;
    }

    // Get the email from localStorage (saved when we sent the link)
    let email = window.localStorage.getItem("emailForSignIn");
    if (!email) {
      // User opened the link on a different device — ask for their email
      email = window.prompt("Please enter your email to confirm sign-in:");
      if (!email) return false;
    }

    try {
      showLoading("Signing in...");
      const result = await firebase.auth().signInWithEmailLink(email, window.location.href);
      const idToken = await result.user.getIdToken();

      // Verify with our backend
      const resp = await fetch("/api/auth/verify-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id_token: idToken }),
      });
      const data = await resp.json();
      hideLoading();

      // Clean up
      window.localStorage.removeItem("emailForSignIn");
      // Remove the sign-in link params from the URL so it doesn't retrigger
      window.history.replaceState(null, "", window.location.origin + window.location.pathname);

      if (data.ok && data.user) {
        gIsAuthenticated = true;
        gCurrentUser = data.user;
        gUserId = data.user.id;
        updateHeaderUI();
        return true;
      }
    } catch (e) {
      hideLoading();
      console.error("Email link sign-in error:", e);
      alert("Sign-in failed: " + (e.message || "The link may have expired. Please request a new one."));
    }
    return false;
  }

  /** Show an error message below the email form */
  function showEmailError(msg) {
    const el = $("emailAuthError");
    if (el) {
      el.textContent = msg;
      show(el);
    }
    hide($("emailAuthSuccess"));
  }

  /** Show a success message below the email form */
  function showEmailSuccess(msg) {
    const el = $("emailAuthSuccess");
    if (el) {
      el.textContent = msg;
      show(el);
    }
    hide($("emailAuthError"));
  }

  /** Clear the email form messages */
  function clearEmailError() {
    const el = $("emailAuthError");
    if (el) { el.textContent = ""; hide(el); }
    const s = $("emailAuthSuccess");
    if (s) { s.textContent = ""; hide(s); }
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
          if (gen.status === "succeeded" && gen.audio_url) {
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
    // Track which generation is currently displayed
    gCurrentGenId = gen.id;
    gCurrentGenIsFavourite = !!gen.is_favourite;
    updateFavButton();

    const moodLabel = $("resultMood");
    const audioEl = $("audioEl");
    const downloadBtn = $("downloadBtn");
    const coverImg = $("coverImg");
    const resultTitle = document.querySelector(".result-title");

    if (resultTitle) resultTitle.textContent = gen.title || "YOUR SONG";
    if (moodLabel) moodLabel.textContent = "Mood: " + (gen.mood || "\u2014");

    if (coverImg && gen.cover_url) coverImg.src = gen.cover_url;

    // Playback: use audio_url (backend returns stream-preferred URL)
    if (audioEl && gen.audio_url) {
      audioEl.src = gen.audio_url;
      audioEl.load();
    }

    // Download: only enable with final (non-stream) URL
    const songTitle = gen.title || "drvibey_track";
    if (downloadBtn) {
      if (gen.download_url) {
        downloadBtn.href = gen.download_url;
        downloadBtn.download = songTitle + ".mp3";
        downloadBtn.classList.remove("is-disabled");
        downloadBtn.setAttribute("aria-disabled", "false");
        downloadBtn.textContent = "DOWNLOAD";
      } else {
        downloadBtn.href = "#";
        downloadBtn.classList.add("is-disabled");
        downloadBtn.setAttribute("aria-disabled", "true");
        downloadBtn.textContent = "DOWNLOAD (processing\u2026)";
        // Poll for the final URL in the background
        if (gCurrentGenId) {
          pollDownloadUrl(gCurrentGenId, songTitle);
        }
      }
    }

    setupPlayerListeners();
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
          if (gen.status === "succeeded" && gen.audio_url) {
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
      renderDiagnosis(gProfileJson, gDiagnosisJson);
      hide($("profileCta"));
      show($("profileProceedBtn"));
      switchScreen("screenProfile");
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

    // Clear chat messages
    const chatMessages = $("chatMessages");
    if (chatMessages) chatMessages.innerHTML = "";

    // Reset image upload
    const previewRow = $("imagePreviewRow");
    if (previewRow) previewRow.innerHTML = "";
    hide($("imageCount"));
    hide($("imageSubmitBtn"));

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
        gCurrentQuestion = data.question_number; // 1

        if (data.intro) {
          addChatBubble("assistant", data.intro);
          gChatHistory.push({ role: "assistant", content: data.intro });
        }

        const showQ1 = () => {
          addChatBubble("assistant", data.reply);
          gChatHistory.push({ role: "assistant", content: data.reply });
          showInputForType(data.input_type || "screenshot", data.skippable || false);
        };

        if (data.intro) {
          addTypingIndicator();
          setTimeout(() => {
            removeTypingIndicator();
            showQ1();
          }, 800);
        } else {
          showQ1();
        }
      }
    } catch (e) {
      removeTypingIndicator();
      console.error("Chat init error:", e);
      const fallbackIntro = "Hey, I'm drVibey \u2014 your personal music psychologist. I'm here to heal you through the power of music... but first, I need to understand your diagnosis.";
      const fallbackQ1 = "Drop me 3-10 screenshots of your favorite playlists, most-played songs, or your Year Wrapped from Spotify, Apple Music, YouTube Music \u2014 whatever you use.";
      addChatBubble("assistant", fallbackIntro);
      gChatHistory.push({ role: "assistant", content: fallbackIntro });
      setTimeout(() => {
        addChatBubble("assistant", fallbackQ1);
        gCurrentQuestion = 1;
        gChatHistory.push({ role: "assistant", content: fallbackQ1 });
        showInputForType("screenshot", false);
      }, 800);
    }
  }

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
        addChatBubble("assistant", data.reply);
        gChatHistory.push({ role: "assistant", content: data.reply });

        if (data.input_type === "buttons" && data.button_options) {
          renderButtonOptions(data.button_options);
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

    showLoading("Analyzing your playlists...");
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

  // ── Button select (Q9/Q10) ─────────────────────────────────────────
  function renderButtonOptions(options) {
    const container = $("buttonOptions");
    if (!container) return;

    container.innerHTML = "";

    options.forEach((label) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "option-btn";
      btn.textContent = label;
      btn.addEventListener("click", () => {
        sendUserAnswer(label);
      });
      container.appendChild(btn);
    });
  }

  // ── Profile synthesis ──────────────────────────────────────────────
  async function buildProfile() {
    showLoading("Building your musical DNA...");

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

        renderDiagnosis(data.profile, data.diagnosis);

        // If already authenticated, hide signup CTA, show proceed button
        if (gIsAuthenticated) {
          hide($("profileCta"));
          show($("profileProceedBtn"));
        } else {
          show($("profileCta"));
          hide($("profileProceedBtn"));
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
    const archEl = $("diagArchetype");
    const textEl = $("diagText");
    const dimEl = $("diagDimensions");
    const genresEl = $("diagGenres");
    const artistsEl = $("diagArtists");

    if (archEl) archEl.textContent = diagnosis.archetype || "The Music Lover";
    if (textEl) textEl.textContent = diagnosis.diagnosis_text || profile.summary || "";

    if (dimEl) {
      dimEl.innerHTML = "";
      const dimensions = [
        {
          label: "Emotional Depth",
          value: (profile.emotional_profile && profile.emotional_profile.emotional_depth) || 0.5,
        },
        { label: "Discovery Drive", value: profile.discovery_drive || 0.5 },
        {
          label: "Energy Range",
          value: (profile.energy_range && profile.energy_range.high) || 0.5,
        },
      ];

      dimensions.forEach((dim) => {
        const row = document.createElement("div");
        row.className = "dimension-row";

        const label = document.createElement("span");
        label.className = "dimension-label";
        label.textContent = dim.label;

        const barWrap = document.createElement("div");
        barWrap.className = "dimension-bar-wrap";

        const bar = document.createElement("div");
        bar.className = "dimension-bar";
        bar.style.width = Math.round(dim.value * 100) + "%";

        const pct = document.createElement("span");
        pct.className = "dimension-pct";
        pct.textContent = Math.round(dim.value * 100) + "%";

        barWrap.appendChild(bar);
        row.appendChild(label);
        row.appendChild(barWrap);
        row.appendChild(pct);
        dimEl.appendChild(row);
      });
    }

    if (genresEl && profile.dominant_genres && profile.dominant_genres.length) {
      genresEl.innerHTML = "<strong>Genres:</strong> " +
        profile.dominant_genres.concat(profile.subgenres || []).join(", ");
    }

    if (artistsEl && profile.identity_artists && profile.identity_artists.length) {
      artistsEl.innerHTML = "<strong>Core Artists:</strong> " +
        profile.identity_artists.join(", ");
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
  async function handleGenerate() {
    showLoading("Generating your track...\n(20 - 30 seconds on average.)");

    try {
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
        }),
      });

      const data = await resp.json();

      if (!resp.ok && resp.status === 403) {
        hideLoading();
        // User needs to sign in first
        const confirmed = confirm("You need to sign in to generate music. Sign in now?");
        if (confirmed) {
          const success = await signInWithGoogle();
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
      console.error(e);
      alert("Failed to start generation: " + e.message);
      hideLoading();
    }
  }

  function pollGeneration(genId) {
    const interval = setInterval(async () => {
      try {
        const resp = await fetch("/api/generation/" + genId);
        const data = await resp.json();

        if (!data.ok) return;

        if (data.status === "succeeded") {
          clearInterval(interval);
          hideLoading();
          gCurrentGenId = genId;
          gCurrentGenIsFavourite = false;
          updateFavButton();
          renderResult(data.result);
          switchScreen("screenResult");
        } else if (data.status === "failed") {
          clearInterval(interval);
          hideLoading();
          alert("Generation failed: " + (data.error || "Unknown error"));
        }
      } catch (e) {
        console.error("Polling error", e);
      }
    }, 3000);
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
    const downloadBtn = $("downloadBtn");
    const coverImg = $("coverImg");
    const resultTitle = document.querySelector(".result-title");

    if (resultTitle && song.title) resultTitle.textContent = song.title;
    if (moodLabel) moodLabel.textContent = "Mood: " + (gSelectedMoodLabel || gSelectedMoodId || "\u2014");

    // Playback URL: prefer stream (plays immediately) then final
    const streamUrl = song.streamAudioUrl || song.sourceStreamAudioUrl ||
      song.stream_audio_url || song.source_stream_audio_url || "";
    const finalUrl = song.audioUrl || song.sourceAudioUrl ||
      song.audio_url || song.source_audio_url || "";
    const playbackUrl = streamUrl || finalUrl;

    if (audioEl && playbackUrl) { audioEl.src = playbackUrl; audioEl.load(); }

    // Download button: only enable with final (non-stream) URL
    const songTitle = song.title || "drvibey_track";
    if (downloadBtn) {
      if (finalUrl) {
        downloadBtn.href = finalUrl;
        downloadBtn.download = songTitle + ".mp3";
        downloadBtn.classList.remove("is-disabled");
        downloadBtn.setAttribute("aria-disabled", "false");
        downloadBtn.textContent = "DOWNLOAD";
      } else {
        downloadBtn.href = "#";
        downloadBtn.classList.add("is-disabled");
        downloadBtn.setAttribute("aria-disabled", "true");
        downloadBtn.textContent = "DOWNLOAD (processing\u2026)";
        // Poll for the final URL in the background
        if (gCurrentGenId) {
          pollDownloadUrl(gCurrentGenId, songTitle);
        }
      }
    }

    const imageUrl = song.imageUrl || song.sourceImageUrl || song.image_url || song.source_image_url || "";
    if (coverImg && imageUrl) coverImg.src = imageUrl;

    setupPlayerListeners();
  }

  /**
   * Poll GET /api/generation/<id>/download-url every 10s until the final
   * MP3 URL is available, then enable the download button.
   * Stops after 18 attempts (~3 minutes) or when the user navigates away.
   */
  function pollDownloadUrl(genId, songTitle) {
    // Clear any previous poll
    if (gDownloadPollTimer) {
      clearInterval(gDownloadPollTimer);
      gDownloadPollTimer = null;
    }

    let attempts = 0;
    const maxAttempts = 18;

    gDownloadPollTimer = setInterval(async () => {
      attempts++;

      // Stop if user navigated to a different generation
      if (gCurrentGenId !== genId) {
        clearInterval(gDownloadPollTimer);
        gDownloadPollTimer = null;
        return;
      }

      try {
        const resp = await fetch("/api/generation/" + genId + "/download-url");
        const data = await resp.json();

        if (data.ok && data.download_url) {
          clearInterval(gDownloadPollTimer);
          gDownloadPollTimer = null;

          const downloadBtn = $("downloadBtn");
          if (downloadBtn) {
            downloadBtn.href = data.download_url;
            downloadBtn.download = (songTitle || "drvibey_track") + ".mp3";
            downloadBtn.classList.remove("is-disabled");
            downloadBtn.setAttribute("aria-disabled", "false");
            downloadBtn.textContent = "DOWNLOAD";
          }
          return;
        }
      } catch (e) {
        console.error("Download URL poll error:", e);
      }

      if (attempts >= maxAttempts) {
        clearInterval(gDownloadPollTimer);
        gDownloadPollTimer = null;
      }
    }, 10000);
  }

  function setupPlayerListeners() {
    const audioEl = $("audioEl");
    const playPauseBtn = $("playPauseBtn");
    const seek = $("seek");
    const tCur = $("tCur");
    const tDur = $("tDur");

    if (!audioEl) return;

    function updateTimes() {
      if (tCur) tCur.textContent = msToTime(audioEl.currentTime || 0);
      if (tDur && isFinite(audioEl.duration)) tDur.textContent = msToTime(audioEl.duration || 0);
      if (seek && isFinite(audioEl.duration) && audioEl.duration > 0) {
        seek.value = String((audioEl.currentTime / audioEl.duration) * 100);
      }
    }

    audioEl.addEventListener("timeupdate", updateTimes);
    audioEl.addEventListener("loadedmetadata", updateTimes);
    audioEl.addEventListener("ended", () => {
      if (playPauseBtn) playPauseBtn.textContent = "\u25B6";
    });

    if (playPauseBtn) {
      const newBtn = playPauseBtn.cloneNode(true);
      playPauseBtn.parentNode.replaceChild(newBtn, playPauseBtn);

      newBtn.addEventListener("click", async () => {
        if (audioEl.paused) {
          try {
            await audioEl.play();
            newBtn.textContent = "\u275A\u275A";
          } catch (e) { console.error("Play error", e); }
        } else {
          audioEl.pause();
          newBtn.textContent = "\u25B6";
        }
      });
    }

    if (seek) {
      seek.addEventListener("input", (e) => {
        if (isFinite(audioEl.duration)) {
          audioEl.currentTime = (e.target.value / 100) * audioEl.duration;
        }
      });
    }
  }

  // ==================================================================
  // INIT
  // ==================================================================
  document.addEventListener("DOMContentLoaded", async () => {
    gUserId = document.body.dataset.userId;

    // Determine initial screen with robust error handling
    // so the user never sees a blank screen
    let initialScreenDone = false;
    try {
      // 1) Check if this is a magic-link redirect (email sign-in completion)
      const emailSignedIn = await completeEmailSignIn();
      if (emailSignedIn) {
        loadSavedProfile();  // load vibe in background (no await needed)
        loadRecentGenerations();
        switchScreen("screenHome");
        initialScreenDone = true;
      }
    } catch (e) {
      console.error("Email sign-in completion error (non-fatal):", e);
    }

    if (!initialScreenDone) {
      try {
        // 2) Check normal auth state
        const isAuth = await checkAuthState();
        if (isAuth) {
          loadSavedProfile();  // load vibe in background
          loadRecentGenerations();
          switchScreen("screenHome");
          initialScreenDone = true;
        }
      } catch (e) {
        console.error("Auth state check error (non-fatal):", e);
      }
    }

    if (!initialScreenDone) {
      // Fallback: anonymous user → go straight to chat
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
          switchScreen("screenLogin");
        }
      });
    }

    const headerUserBtn = $("headerUserBtn");
    if (headerUserBtn) {
      headerUserBtn.addEventListener("click", () => {
        if (gIsAuthenticated) {
          toggleDropdown();
        } else {
          switchScreen("screenLogin");
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

    // ── Login screen handlers ──
    const loginGoogle = $("loginGoogle");
    if (loginGoogle) {
      loginGoogle.addEventListener("click", async () => {
        const success = await signInWithGoogle();
        if (success) {
          loadRecentGenerations();
          switchScreen("screenHome");
        }
      });
    }

    // ── Email link auth form handler ──
    const emailAuthForm = $("emailAuthForm");

    if (emailAuthForm) {
      emailAuthForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        clearEmailError();
        const email = ($("emailInput").value || "").trim();
        if (!email) return;

        const sent = await sendEmailSignInLink(email);
        if (sent) {
          showEmailSuccess("Check your inbox! We sent a sign-in link to " + email);
          // Disable the button so they don't spam
          const btn = $("emailSendLinkBtn");
          if (btn) { btn.disabled = true; btn.textContent = "Link sent!"; }
        }
      });
    }

    const loginSkip = $("loginSkip");
    if (loginSkip) {
      loginSkip.addEventListener("click", () => {
        switchScreen("screenChat");
        initChat();
      });
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
    const skipSignup = $("skipSignup");
    if (skipSignup) {
      skipSignup.addEventListener("click", () => {
        switchScreen("screenMood");
        setTimeout(() => autoSelectMood(), 100);
      });
    }

    // Profile screen signup buttons -> Firebase auth
    const signupGoogle = $("signupGoogle");
    if (signupGoogle) {
      signupGoogle.addEventListener("click", async () => {
        const success = await signInWithGoogle();
        if (success) {
          switchScreen("screenMood");
          setTimeout(() => autoSelectMood(), 100);
        }
      });
    }

    // Profile screen email signup -> go to login screen
    const signupEmail = $("signupEmail");
    if (signupEmail) {
      signupEmail.addEventListener("click", () => {
        switchScreen("screenLogin");
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

    // ── Result screen: Favourite button ──
    const favBtn = $("favBtn");
    if (favBtn) {
      favBtn.addEventListener("click", toggleFavourite);
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
    const bpmSlider = $("bpmSlider");
    const bpmValue = $("bpmValue");
    const extrasNextBtn = $("extrasNextBtn");

    function updateExtrasButton() {
      const hasSong = songRefInput && songRefInput.value.trim() !== "";
      const hasGenre = genreSelect && genreSelect.value !== "";
      const hasBpm = bpmSlider && parseInt(bpmSlider.value, 10) > 0;
      if (extrasNextBtn) {
        extrasNextBtn.textContent = (hasSong || hasGenre || hasBpm) ? "NEXT >" : "SKIP >";
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
    if (bpmSlider) {
      bpmSlider.value = "0";
      bpmSlider.addEventListener("input", () => {
        const v = parseInt(bpmSlider.value, 10);
        if (v >= 60) { gBpm = v; if (bpmValue) bpmValue.textContent = v; }
        else { gBpm = null; if (bpmValue) bpmValue.textContent = "--"; }
        updateExtrasButton();
      });
    }

    if (extrasNextBtn) {
      extrasNextBtn.addEventListener("click", () => handleGenerate());
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
