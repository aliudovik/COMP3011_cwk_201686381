/**
 * Psychoacoustic Personality Assessment — Frontend Logic
 * Handles: test config fetch, audio playback, slider interaction,
 * question progression, submission, and result rendering.
 */
(function () {
  "use strict";

  // ── Helpers ──────────────────────────────────────────────────────
  function $(id) { return document.getElementById(id); }
  function show(el) { if (el) el.classList.remove("is-hidden"); }
  function hide(el) { if (el) el.classList.add("is-hidden"); }

  var PSYCH_ARCHETYPE_CATALOG = {
    MELT: { name: "The Elegant Architect", description: "Precise, polished, and emotionally grounded craftsmanship." },
    MELG: { name: "The Refined Provocateur", description: "Structured execution with avant-garde risk-taking." },
    MEDT: { name: "The Velvet Strategist", description: "Warm emotional intelligence with intentional design." },
    MEDG: { name: "The Harmonic Visionary", description: "Beautiful systems that still break creative boundaries." },
    MKLT: { name: "The Quiet Insurgent", description: "Calm exterior, deep conviction, and layered meaning." },
    MKLG: { name: "The Structured Rebel", description: "Disciplined process used to challenge conventions." },
    MKDT: { name: "The Intimate Alchemist", description: "Vulnerability transformed into deeply personal art." },
    MKDG: { name: "The Dark Innovator", description: "Experimental form wrapped around emotional truth." },
    BELT: { name: "The Passionate Commander", description: "High-energy leadership with story-driven intent." },
    BELG: { name: "The Showrunner", description: "Big-scale spectacle, originality, and social momentum." },
    BEDT: { name: "The Magnetic Storyteller", description: "Cinematic emotion and relatable narrative pull." },
    BEDG: { name: "The Supernova", description: "Maximal creative force, bold identity, unforgettable impact." },
    BKLT: { name: "The Raw Philosopher", description: "Analytical depth delivered with unfiltered edge." },
    BKLG: { name: "The Glitch Architect", description: "System-level experimentation and future-facing thinking." },
    BKDT: { name: "The Wounded Healer", description: "Radical honesty that turns pain into connection." },
    BKDG: { name: "The Chaos Oracle", description: "Uncompromising originality and dream-logic invention." },
  };

  function renderPsychTypeCatalog() {
    var wrap = $("psychTypeCatalog");
    if (!wrap) return;
    wrap.innerHTML = "";
    Object.keys(PSYCH_ARCHETYPE_CATALOG).forEach(function (code) {
      var meta = PSYCH_ARCHETYPE_CATALOG[code];
      var row = document.createElement("div");
      row.className = "diag-type-item";

      var c = document.createElement("div");
      c.className = "diag-type-item-code";
      c.textContent = code;

      var n = document.createElement("div");
      n.className = "diag-type-item-name";
      n.textContent = meta.name;

      var d = document.createElement("div");
      d.className = "diag-type-item-desc";
      d.textContent = meta.description;

      row.appendChild(c);
      row.appendChild(n);
      row.appendChild(d);
      wrap.appendChild(row);
    });
  }

  function openPsychTypeModal() {
    var modal = $("psychTypeModal");
    if (!modal) return;
    show(modal);
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
  }

  function closePsychTypeModal() {
    var modal = $("psychTypeModal");
    if (!modal) return;
    hide(modal);
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
  }

  // ── State ────────────────────────────────────────────────────────
  var _config = null;          // { audio_questions, text_questions, total_questions }
  var _allQuestions = [];       // merged & ordered: audio first, then text
  var _currentIdx = 0;
  var _answers = [];            // { id, value, swapped, type:"audio"|"text" }
  var _audioLeft = null;        // HTMLAudioElement
  var _audioRight = null;
  var _leftPlayed = false;
  var _rightPlayed = false;
  var _sliderTouched = false;
  var _isSubmitting = false;

  // ── Public API (called from public.js) ───────────────────────────
  window.PsychoacousticTest = {
    start: startTest,
    renderResult: renderPsychProfile,
  };

  // ── Start test ───────────────────────────────────────────────────
  async function startTest() {
    // Reset state
    _currentIdx = 0;
    _answers = [];
    _leftPlayed = false;
    _rightPlayed = false;
    _sliderTouched = false;
    _isSubmitting = false;

    // Hide chat elements, show test UI
    hide($("chatMessages"));
    hide($("chatInputWrap"));
    hide($("skipWrap"));
    hide($("imageUploadWrap"));
    hide($("chipSelectWrap"));
    hide($("buttonSelectWrap"));
    hide($("multiButtonSelectWrap"));
    hide($("pathChoiceWrap"));
    var chatProgress = document.querySelector(".chat-progress");
    if (chatProgress) chatProgress.classList.add("is-temporarily-hidden");

    show($("psychTestWrap"));

    try {
      var resp = await fetch("/api/psychoacoustic/config");
      var data = await resp.json();
      if (!data.ok) {
        console.error("Psych config error:", data.error);
        return;
      }
      _config = data;
      // Audio questions first, then text
      _allQuestions = [];
      data.audio_questions.forEach(function (q) {
        q._type = "audio";
        _allQuestions.push(q);
      });
      data.text_questions.forEach(function (q) {
        q._type = "text";
        _allQuestions.push(q);
      });

      _renderQuestion();
    } catch (e) {
      console.error("Failed to load psychoacoustic config:", e);
    }
  }

  // ── Render current question ──────────────────────────────────────
  function _renderQuestion() {
    if (_currentIdx >= _allQuestions.length) {
      _submitTest();
      return;
    }

    var q = _allQuestions[_currentIdx];
    var total = _allQuestions.length;

    // Update progress
    $("psychProgressText").textContent = "Question " + (_currentIdx + 1) + " of " + total;
    $("psychProgressFill").style.width = ((_currentIdx / total) * 100) + "%";

    // Reset slider state
    _sliderTouched = false;
    $("psychNextBtn").disabled = true;

    if (q._type === "audio") {
      _renderAudioQuestion(q);
    } else {
      _renderTextQuestion(q);
    }
  }

  // ── Audio question ───────────────────────────────────────────────
  function _renderAudioQuestion(q) {
    show($("psychAudioCard"));
    hide($("psychTextCard"));

    $("psychAudioPrompt").textContent = q.prompt;

    // Reset play states
    _leftPlayed = false;
    _rightPlayed = false;
    _stopAllAudio();

    var leftPanel = document.querySelector(".psych-player-left");
    var rightPanel = document.querySelector(".psych-player-right");
    leftPanel.classList.remove("is-playing", "has-played");
    rightPanel.classList.remove("is-playing", "has-played");
    $("psychLeftStatus").textContent = "Tap to play";
    $("psychRightStatus").textContent = "Tap to play";

    // Reset play button icons
    $("psychPlayLeft").innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>';
    $("psychPlayRight").innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>';
    $("psychPlayLeft").classList.remove("is-playing");
    $("psychPlayRight").classList.remove("is-playing");

    // Reset slider
    var slider = $("psychAudioSlider");
    slider.value = 3;
    slider.disabled = true;

    // Create audio elements
    _audioLeft = new Audio("/static/test-audio/" + q.left_file);
    _audioRight = new Audio("/static/test-audio/" + q.right_file);
    _audioLeft.loop = true;
    _audioRight.loop = true;

    // Enable slider once both clips have been played at least once
    function _checkBothPlayed() {
      if (_leftPlayed && _rightPlayed) {
        slider.disabled = false;
      }
    }

    _audioLeft.addEventListener("playing", function () {
      _leftPlayed = true;
      leftPanel.classList.add("is-playing");
      leftPanel.classList.add("has-played");
      $("psychLeftStatus").textContent = "Playing...";
      $("psychPlayLeft").classList.add("is-playing");
      $("psychPlayLeft").innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';
      _checkBothPlayed();
    });

    _audioLeft.addEventListener("pause", function () {
      leftPanel.classList.remove("is-playing");
      $("psychLeftStatus").textContent = "Played";
      $("psychPlayLeft").classList.remove("is-playing");
      $("psychPlayLeft").innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>';
    });

    _audioRight.addEventListener("playing", function () {
      _rightPlayed = true;
      rightPanel.classList.add("is-playing");
      rightPanel.classList.add("has-played");
      $("psychRightStatus").textContent = "Playing...";
      $("psychPlayRight").classList.add("is-playing");
      $("psychPlayRight").innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';
      _checkBothPlayed();
    });

    _audioRight.addEventListener("pause", function () {
      rightPanel.classList.remove("is-playing");
      $("psychRightStatus").textContent = "Played";
      $("psychPlayRight").classList.remove("is-playing");
      $("psychPlayRight").innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>';
    });
  }

  function _toggleLeft() {
    if (!_audioLeft) return;
    // Pause the other
    if (_audioRight && !_audioRight.paused) _audioRight.pause();
    _audioLeft.currentTime = 0;
    _audioLeft.play().catch(function () {});
  }

  function _toggleRight() {
    if (!_audioRight) return;
    if (_audioLeft && !_audioLeft.paused) _audioLeft.pause();
    _audioRight.currentTime = 0;
    _audioRight.play().catch(function () {});
  }

  function _stopAllAudio() {
    if (_audioLeft) { _audioLeft.pause(); _audioLeft.src = ""; _audioLeft = null; }
    if (_audioRight) { _audioRight.pause(); _audioRight.src = ""; _audioRight = null; }
  }

  // ── Text question ────────────────────────────────────────────────
  function _renderTextQuestion(q) {
    hide($("psychAudioCard"));
    show($("psychTextCard"));
    _stopAllAudio();

    $("psychTextPrompt").textContent = q.text;
    $("psychOptionA").textContent = q.left_option;
    $("psychOptionB").textContent = q.right_option;

    var slider = $("psychTextSlider");
    slider.value = 3;
    slider.disabled = false;  // Text questions don't require pre-listen
  }

  // ── Slider handlers ──────────────────────────────────────────────
  function _onAudioSliderInput() {
    _sliderTouched = true;
    $("psychNextBtn").disabled = false;
  }

  function _onTextSliderInput() {
    _sliderTouched = true;
    $("psychNextBtn").disabled = false;
  }

  // ── Next button ──────────────────────────────────────────────────
  function _onNext() {
    if (!_sliderTouched) return;

    var q = _allQuestions[_currentIdx];
    var slider;
    if (q._type === "audio") {
      slider = $("psychAudioSlider");
    } else {
      slider = $("psychTextSlider");
    }

    _answers.push({
      id: q.id,
      value: parseInt(slider.value, 10),
      swapped: q.swapped || false,
      type: q._type,
    });

    _stopAllAudio();
    _currentIdx++;
    _renderQuestion();
  }

  // ── Submit test ──────────────────────────────────────────────────
  async function _submitTest() {
    if (_isSubmitting) return;
    _isSubmitting = true;

    hide($("psychAudioCard"));
    hide($("psychTextCard"));
    $("psychNextBtn").disabled = true;
    $("psychProgressFill").style.width = "100%";
    $("psychProgressText").textContent = "Analyzing your results...";

    var audioAnswers = _answers.filter(function (a) { return a.type === "audio"; });
    var textAnswers = _answers.filter(function (a) { return a.type === "text"; });

    // Get user_id from body data attribute
    var userId = document.body.dataset.userId || null;

    try {
      var resp = await fetch("/api/psychoacoustic/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: userId,
          audio_answers: audioAnswers.map(function (a) { return { id: a.id, value: a.value, swapped: a.swapped }; }),
          text_answers: textAnswers.map(function (a) { return { id: a.id, value: a.value, swapped: a.swapped }; }),
        }),
      });
      var data = await resp.json();

      if (data.ok) {
        // Store profile data globally for generation pipeline + My Vibe
        if (window.setPsychProfileData) {
          window.setPsychProfileData(data);
        }
        renderPsychProfile(data.diagnosis);
      } else {
        console.error("Psych submit error:", data.error);
        $("psychProgressText").textContent = "Error: " + (data.error || "Unknown error");
      }
    } catch (e) {
      console.error("Psych submit failed:", e);
      $("psychProgressText").textContent = "Connection error. Please try again.";
    }

    _isSubmitting = false;
  }

  // ── Render result screen ─────────────────────────────────────────
  function renderPsychProfile(diagnosis) {
    if (!diagnosis) return;

    // Switch to the psychoacoustic profile screen
    if (window.switchScreen) {
      window.switchScreen("screenPsychProfile");
    }

    // Code + title
    $("psychCode").textContent = diagnosis.code || "----";
    $("psychTitle").textContent = diagnosis.title || "Your Profile";

    // Axis bars
    var axesContainer = $("psychAxes");
    axesContainer.innerHTML = "";

    var axisScores = diagnosis.axis_scores || {};
    var axisOrder = ["1", "2", "3", "4"];
    axisOrder.forEach(function (axNum) {
      var ax = axisScores[axNum];
      if (!ax) return;

      var row = document.createElement("div");
      row.className = "psych-axis-row";

      var header = document.createElement("div");
      header.className = "psych-axis-header";

      var nameEl = document.createElement("span");
      nameEl.className = "psych-axis-name";
      nameEl.textContent = ax.axis_name;

      var resultEl = document.createElement("span");
      resultEl.className = "psych-axis-result";
      resultEl.textContent = Math.round(ax.percentage) + "% " + ax.dominant_pole + " (" + ax.letter + ")";

      header.appendChild(nameEl);
      header.appendChild(resultEl);

      var track = document.createElement("div");
      track.className = "psych-axis-bar-track";

      var fill = document.createElement("div");
      fill.className = "psych-axis-bar-fill";
      fill.style.width = "0%";
      track.appendChild(fill);

      row.appendChild(header);
      row.appendChild(track);
      axesContainer.appendChild(row);

      // Animate bar fill
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          fill.style.width = Math.round(ax.percentage) + "%";
        });
      });
    });

    // Sections
    var sections = diagnosis.sections || {};
    if ($("psychSecWork"))   $("psychSecWork").textContent = sections.how_you_work || "";
    if ($("psychSecPower"))  $("psychSecPower").textContent = sections.superpower || "";
    if ($("psychSecHeel"))   $("psychSecHeel").textContent = sections.achilles_heel || "";
    if ($("psychSecStudio")) $("psychSecStudio").textContent = sections.perfect_studio || "";
  }

  // ── Event binding ────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", function () {
    // Play buttons
    var playLeft = $("psychPlayLeft");
    var playRight = $("psychPlayRight");
    if (playLeft) playLeft.addEventListener("click", _toggleLeft);
    if (playRight) playRight.addEventListener("click", _toggleRight);

    // Sliders
    var audioSlider = $("psychAudioSlider");
    var textSlider = $("psychTextSlider");
    if (audioSlider) audioSlider.addEventListener("input", _onAudioSliderInput);
    if (textSlider) textSlider.addEventListener("input", _onTextSliderInput);

    // Next button
    var nextBtn = $("psychNextBtn");
    if (nextBtn) nextBtn.addEventListener("click", _onNext);

    renderPsychTypeCatalog();

    var psychTypeInfoBtn = $("psychTypeInfoBtn");
    if (psychTypeInfoBtn) {
      psychTypeInfoBtn.addEventListener("click", openPsychTypeModal);
    }

    var psychTypeModalClose = $("psychTypeModalClose");
    if (psychTypeModalClose) {
      psychTypeModalClose.addEventListener("click", closePsychTypeModal);
    }

    var psychTypeModalBackdrop = $("psychTypeModalBackdrop");
    if (psychTypeModalBackdrop) {
      psychTypeModalBackdrop.addEventListener("click", closePsychTypeModal);
    }

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closePsychTypeModal();
    });
  });
})();
