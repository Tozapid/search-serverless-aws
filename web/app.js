// Scheduled search: queries that run on a schedule in several search engines
// and send what they find by email, shortened by an AI model if a search wants it.
// The page signs in with Cognito and calls /api with the ID token; the API is
// in lambda/search.py. The texts are in i18n.js.
(function () {
  const config = window.SEARCH_CONFIG || {};
  const i18n = window.SEARCH_I18N;
  const root = document.getElementById("root");
  const state = {
    me: null,
    items: [],
    destinations: {},
    production: true,
    // Whose searches the list shows: "mine", "all" or the email of a user.
    // Only administrators see anything but their own.
    view: "mine",
    users: [],
    // The combined digest of the user whose searches are shown; none for
    // the list of all users.
    digest: null,
  };
  // The name of the site in authenticator apps.
  const ISSUER = "Search " + window.location.hostname;
  // The open source project of this page, linked from the sign-in page.
  const REPOSITORY = "https://github.com/Tozapid/search-serverless-aws";
  // The language is the Cognito attribute "locale" of the user; the browser
  // keeps a copy so that the sign-in page speaks it too.
  const LANGUAGE_KEY = "search-language";
  const VIEW_KEY = "search-view";
  const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];
  let language = i18n.fallback;
  let pool = null;
  let toastTimer = null;

  // Language ---------------------------------------------------------------

  function known(code) {
    return Boolean(code && i18n.strings[code]);
  }

  // The text of a key in the current language with its {name} parts filled.
  function t(key, params) {
    let text = i18n.strings[language][key] ?? i18n.strings[i18n.fallback][key] ?? key;
    if (typeof text === "object") {
      text = text[new Intl.PluralRules(language).select(params.count)] ?? text.other;
    }
    return text.replace(/\{(\w+)\}/g, (part, name) => (params && name in params ? String(params[name]) : part));
  }

  function setLanguage(code) {
    if (!known(code)) return;
    language = code;
    document.documentElement.lang = code;
    document.title = t("brand");
    try {
      localStorage.setItem(LANGUAGE_KEY, code);
    } catch (error) {
      // Private windows may refuse storage; the language holds until reload.
    }
  }

  function storedView() {
    try {
      return localStorage.getItem(VIEW_KEY);
    } catch (error) {
      return null;
    }
  }

  function storedLanguage() {
    try {
      return localStorage.getItem(LANGUAGE_KEY);
    } catch (error) {
      return null;
    }
  }

  // The first language of the browser this page speaks, else the fallback.
  function browserLanguage() {
    const found = (navigator.languages || [navigator.language]).map((code) => String(code).slice(0, 2)).find(known);
    return found || i18n.fallback;
  }

  async function loadLanguage() {
    try {
      const { user } = await currentUser();
      const attributes = await cognito((done) => user.getUserAttributes(done));
      const locale = (attributes || []).find((attribute) => attribute.getName() === "locale");
      if (locale) setLanguage(locale.getValue());
    } catch (error) {
      // The language of this browser stays.
    }
  }

  async function saveLanguage(code) {
    setLanguage(code);
    try {
      const { user } = await currentUser();
      await cognito((done) => user.updateAttributes([{ Name: "locale", Value: code }], done));
      toast(t("language.saved"));
    } catch (error) {
      toast(authMessage(error));
    }
  }

  // Page helpers -----------------------------------------------------------

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value == null || value === false) return;
      if (key === "class") node.className = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (key === "value") node.value = value;
      else node.setAttribute(key, value === true ? "" : value);
    });
    [].concat(children || []).forEach((child) => {
      if (child == null || child === false) return;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  // replaceChildren() turns null into the text "null"; empty parts are left out.
  function fill(node, children) {
    node.replaceChildren(...[].concat(children).filter(Boolean));
  }

  function toast(message) {
    let node = document.querySelector(".toast");
    if (!node) {
      node = el("div", { class: "toast", role: "status" });
      document.body.append(node);
    }
    node.textContent = message;
    node.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.add("hidden"), 3500);
  }

  function when(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString(language, { dateStyle: "short", timeStyle: "short" });
  }

  // A modal window over the page. The page below may re-render; the dialog
  // stays and fill() replaces its contents.
  function dialog(title, onClose) {
    const body = el("div", { class: "modal-body" });
    const heading = el("h2", {}, title);
    const node = el("dialog", { class: "modal" }, [
      el("div", { class: "modal-head" }, [
        heading,
        el("button", { type: "button", class: "icon-button close", "aria-label": t("close"), onclick: () => node.close() }, "×"),
      ]),
      body,
    ]);
    node.addEventListener("close", () => {
      node.remove();
      if (onClose) onClose();
    });
    node.addEventListener("click", (event) => {
      if (event.target === node) node.close();
    });
    document.body.append(node);
    node.showModal();
    return {
      node,
      close: () => node.close(),
      open: () => node.isConnected,
      title: (text) => { heading.textContent = text; },
      fill: (children) => fill(body, children),
    };
  }

  // Names --------------------------------------------------------------------

  function engineName(id) {
    const engine = ((state.me && state.me.engines) || []).find((item) => item.id === id);
    return engine ? engine.label : id;
  }

  // "ru-RU" as "Russian (Russia)" in the language of the page.
  function marketName(market) {
    const [lang, region] = market.split("-");
    try {
      const languages = new Intl.DisplayNames([language], { type: "language" });
      const regions = new Intl.DisplayNames([language], { type: "region" });
      return `${languages.of(lang)} (${regions.of(region)})`;
    } catch (error) {
      return market;
    }
  }

  // Monday is 0, as in the API. 1 January 2024 was a Monday.
  function dayName(day, width) {
    return new Date(Date.UTC(2024, 0, 1 + day, 12)).toLocaleDateString(language, { weekday: width || "short", timeZone: "UTC" });
  }

  function hourName(hour) {
    return new Date(Date.UTC(2024, 0, 1, hour)).toLocaleTimeString(language, { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  }

  function scheduleText(item) {
    const schedule = item.schedule || {};
    if (schedule.type === "hours") return t("schedule.everyHours", { count: schedule.every });
    const days = schedule.days || [];
    const hour = hourName(schedule.hour);
    if (days.length === 7) return t("schedule.dailyAt", { hour });
    if (days.join() === "0,1,2,3,4") return t("schedule.weekdaysAt", { hour });
    return t("schedule.daysAt", { days: days.map((day) => dayName(day)).join(", "), hour });
  }

  function timeZones() {
    try {
      return Intl.supportedValuesOf("timeZone");
    } catch (error) {
      return [];
    }
  }

  function browserZone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch (error) {
      return "UTC";
    }
  }

  function statusText(status) {
    return status ? t("status." + status) : t("status.never");
  }

  function statusClass(status) {
    if (status === "ok" || status === "no_mail") return "status-ok";
    if (status === "empty") return "muted";
    return status ? "status-bad" : "muted";
  }

  // Sign-in ----------------------------------------------------------------

  function userPool() {
    if (!pool) {
      pool = new window.AmazonCognitoIdentity.CognitoUserPool({ UserPoolId: config.userPoolId, ClientId: config.clientId });
    }
    return pool;
  }

  // The signed-in user with a valid session, which the account calls need.
  function currentUser() {
    return new Promise((resolve, reject) => {
      const user = userPool().getCurrentUser();
      if (!user) return reject(new Error("auth"));
      user.getSession((error, session) => {
        if (error || !session || !session.isValid()) reject(error || new Error("auth"));
        else resolve({ user, session });
      });
    });
  }

  async function currentSession() {
    return (await currentUser()).session;
  }

  // Wraps a callback-style call of the Cognito library.
  function cognito(call) {
    return new Promise((resolve, reject) => call((error, result) => (error ? reject(error) : resolve(result))));
  }

  function authMessage(error) {
    const code = error && (error.code || error.name);
    if (code === "NotAuthorizedException" || code === "UserNotFoundException") return t("auth.wrong");
    if (code === "InvalidPasswordException") return t("auth.passwordRules");
    if (code === "CodeMismatchException" || code === "EnableSoftwareTokenMFAException") return t("auth.wrongCode");
    if (code === "ExpiredCodeException") return t("auth.expiredCode");
    if (code === "LimitExceededException" || code === "TooManyRequestsException") return t("auth.tooMany");
    return (error && error.message) || t("failed");
  }

  function loginCard(children, onsubmit) {
    root.replaceChildren(el("main", { class: "login" }, el("form", {
      class: "login-card",
      onsubmit: (event) => {
        event.preventDefault();
        onsubmit();
      },
    }, children)));
  }

  function signInCallbacks(user, error) {
    return {
      onSuccess: () => boot(),
      onFailure: (failure) => { error.textContent = authMessage(failure); },
      newPasswordRequired: () => showNewPassword(user),
      totpRequired: () => showTotp(user),
    };
  }

  // The language menu of the sign-in page and of the settings.
  function languagePicker(onchange) {
    return el("select", { class: "lang-select", "aria-label": t("language.title"), onchange: (event) => onchange(event.target.value) },
      i18n.languages.map(([code, name]) => el("option", { value: code, selected: code === language }, name)));
  }

  // In the public demo (config.demo) the sign-in is shown and filled in.
  function showLogin(message) {
    const demo = config.demo || {};
    const error = el("p", { class: "error", role: "alert" }, message || "");
    const email = el("input", { type: "email", autocomplete: "username", required: true, value: demo.email || "" });
    const password = el("input", { type: "password", autocomplete: "current-password", required: true, value: demo.password || "" });
    loginCard([
      el("img", { class: "login-mark", src: "/favicon.svg", alt: "" }),
      el("h1", {}, t("brand")),
      demo.email ? el("p", { class: "demo-note" }, [
        t("demo.signIn", { email: demo.email, password: demo.password }),
        demo.reset ? " " + t("demo.reset") : "",
      ]) : null,
      el("label", {}, [t("login.email"), email]),
      el("label", {}, [t("login.password"), password]),
      el("button", { class: "primary", type: "submit" }, t("login.signIn")),
      error,
      el("div", { class: "login-lang" }, [
        el("a", { href: REPOSITORY, rel: "noopener" }, t("login.source")),
        languagePicker((code) => {
          setLanguage(code);
          showLogin();
        }),
      ]),
    ], () => {
      error.textContent = "";
      const user = new window.AmazonCognitoIdentity.CognitoUser({ Username: email.value.trim(), Pool: userPool() });
      user.authenticateUser(new window.AmazonCognitoIdentity.AuthenticationDetails({
        Username: email.value.trim(),
        Password: password.value,
      }), signInCallbacks(user, error));
    });
    email.focus();
  }

  function showNewPassword(user) {
    const error = el("p", { class: "error", role: "alert" });
    const password = el("input", { type: "password", autocomplete: "new-password", required: true, minlength: "12" });
    loginCard([
      el("h1", {}, t("newPassword.title")),
      el("p", { class: "lede" }, t("newPassword.hint")),
      el("label", {}, [t("password.new"), password]),
      el("button", { class: "primary", type: "submit" }, t("newPassword.submit")),
      error,
    ], () => user.completeNewPasswordChallenge(password.value, {}, signInCallbacks(user, error)));
    password.focus();
  }

  // The second step when two-factor sign-in is on: the code of the
  // authenticator app.
  function showTotp(user) {
    const error = el("p", { class: "error", role: "alert" });
    const code = el("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", pattern: "[0-9]{6}", maxlength: "6", required: true });
    loginCard([
      el("h1", {}, t("totp.title")),
      el("p", { class: "lede" }, t("totp.hint")),
      el("label", {}, [t("totp.code"), code]),
      el("button", { class: "primary", type: "submit" }, t("login.signIn")),
      error,
    ], () => {
      error.textContent = "";
      user.sendMFACode(code.value.trim(), signInCallbacks(user, error), "SOFTWARE_TOKEN_MFA");
    });
    code.focus();
  }

  function signOut() {
    const user = pool && pool.getCurrentUser();
    if (user) user.signOut();
    document.querySelectorAll("dialog.modal").forEach((node) => node.close());
    document.querySelector(".dialog-wrap")?.remove();
    showLogin();
  }

  async function api(path, options) {
    const session = await currentSession().catch(() => null);
    if (!session) {
      showLogin(t("sessionEnded"));
      throw new Error(t("sessionEnded"));
    }
    const response = await fetch(config.apiBase + path, {
      method: (options && options.method) || "GET",
      headers: {
        Authorization: "Bearer " + session.getIdToken().getJwtToken(),
        ...(options && options.body ? { "Content-Type": "application/json" } : {}),
      },
      body: options && options.body ? JSON.stringify(options.body) : undefined,
    });
    let data = {};
    const text = await response.text();
    if (text) {
      try { data = JSON.parse(text); } catch (error) { data = { error: text }; }
    }
    if (response.status === 401) {
      signOut();
      throw new Error(t("sessionEnded"));
    }
    if (!response.ok) {
      // The API names the error with a code; its own text is the English one.
      const translated = data.code && i18n.strings[i18n.fallback]["api." + data.code];
      const message = translated ? t("api." + data.code) : data.error || t("requestFailed");
      throw new Error(data.detail ? message + ": " + data.detail : message);
    }
    return data;
  }

  // Data -------------------------------------------------------------------

  async function load() {
    if (!state.me.admin) state.view = "mine";
    const path = state.view === "mine" ? "/searches"
      : state.view === "all" ? "/admin/searches"
        : "/admin/searches?owner=" + encodeURIComponent(state.view);
    const data = await api(path);
    state.items = data.items || [];
    state.digest = data.digest || null;
    state.destinations = data.destinations || {};
    state.production = Boolean(data.production);
  }

  async function reload() {
    try {
      await load();
    } catch (error) {
      toast(error.message);
    }
    render();
  }

  function find(id) {
    return state.items.find((item) => item.id === id);
  }

  // The API path of one search; a search of another user carries its owner,
  // which only an administrator may name.
  function searchPath(item, rest) {
    const path = "/searches/" + item.id + (rest || "");
    return item.owner && item.owner !== state.me.email ? path + "?owner=" + encodeURIComponent(item.owner) : path;
  }

  function mine(item) {
    return !item.owner || item.owner === state.me.email;
  }

  function setView(view) {
    state.view = view;
    try {
      localStorage.setItem(VIEW_KEY, view);
    } catch (error) {
      // The choice holds until reload.
    }
    reload();
  }

  async function change(item, changes, done) {
    try {
      await api(searchPath(item), { method: "PATCH", body: changes });
      if (done) toast(done);
    } catch (error) {
      toast(error.message);
    }
    await reload();
  }

  // Page -------------------------------------------------------------------

  function render() {
    root.replaceChildren(el("div", { class: "app" }, [
      el("header", { class: "top" }, [
        el("div", { class: "brand" }, [el("img", { class: "brand-mark", src: "/favicon.svg", alt: "" }), el("strong", {}, t("brand")), el("span", {}, location.hostname)]),
        el("div", { class: "who" }, [
          el("span", { title: state.me.email }, state.me.email),
          el("button", { type: "button", class: "icon-button", onclick: () => showAccount() }, t("menu.account")),
          el("button", { type: "button", class: "icon-button", onclick: signOut }, t("menu.signOut")),
        ]),
      ]),
      el("main", { class: "content" }, [
        sandboxNote(),
        digestSection(),
        searchesSection(),
      ]),
    ]));
  }

  function sandboxNote() {
    if (!state.me.mail) {
      return el("p", { class: "note" }, [el("strong", {}, t("noMail.title")), t(config.demo && config.demo.reset ? "noMail.textReset" : "noMail.text")]);
    }
    if (state.production) return null;
    return el("p", { class: "note" }, [el("strong", {}, t("sandbox.title")), t("sandbox.text")]);
  }

  function searchesSection() {
    const active = state.items.filter((item) => item.active).length;
    return el("section", { class: "card" }, [
      el("div", { class: "list-head" }, [
        el("h2", {}, [t("searches.title") + " ", el("span", { class: "muted" }, t("searches.count", { active, total: state.items.length }))]),
        state.me.admin ? viewPicker() : null,
        el("button", {
          type: "button",
          class: "primary",
          disabled: state.view === "mine" && state.items.length >= state.me.max_searches,
          title: state.view !== "mine" ? t("searches.newOwn") : state.items.length >= state.me.max_searches ? t("api.too_many_searches") : null,
          onclick: () => showEditor(null),
        }, t("searches.new")),
      ]),
      state.items.length
        ? el("ul", { class: "rows" }, state.items.map(searchRow))
        : el("p", { class: "muted" }, t("searches.empty")),
    ]);
  }

  // Administrators: own searches, everyone's, or one user's.
  function viewPicker() {
    const users = state.users.map((user) => user.email).filter((email) => email !== state.me.email);
    if (!["mine", "all"].includes(state.view) && !users.includes(state.view)) users.push(state.view);
    return el("select", { class: "view-select", "aria-label": t("view.label"), onchange: (event) => setView(event.target.value) }, [
      el("option", { value: "mine", selected: state.view === "mine" }, t("view.mine")),
      el("option", { value: "all", selected: state.view === "all" }, t("view.all")),
      ...users.map((email) => el("option", { value: email, selected: state.view === email }, email)),
    ]);
  }

  async function loadUsers() {
    if (!state.me.admin) return;
    try {
      state.users = (await api("/admin/users")).items || [];
    } catch (error) {
      state.users = [];
    }
  }

  function searchRow(item) {
    return el("li", { class: "row" + (item.active ? "" : " off") }, [
      el("label", { class: "switch", title: t(item.active ? "search.on" : "search.off") }, el("input", {
        type: "checkbox",
        checked: item.active,
        "aria-label": t(item.active ? "search.turnOff" : "search.turnOn", { name: item.name || item.query }),
        onchange: (event) => change(item, { active: event.target.checked }, t(event.target.checked ? "search.turnedOn" : "search.turnedOff")),
      })),
      el("div", { class: "row-main" }, [
        el("span", { class: "row-title" }, item.name || item.query),
        mine(item) ? null : el("span", { class: "row-owner" }, item.owner),
        el("span", { class: "row-sub muted" }, [
          item.name ? item.query + " · " : "",
          item.in_digest ? t("schedule.inDigest") : scheduleText(item),
          " · ",
          item.engines.map(engineName).join(", "),
          item.ai ? " · AI" : "",
        ]),
      ]),
      el("span", { class: "row-stat muted" }, [
        item.active && item.next_run && !item.in_digest ? el("span", {}, t("search.next", { date: when(item.next_run) })) : null,
        el("span", { class: statusClass(item.last_status) }, item.last_run
          ? t("search.last", { date: when(item.last_run), status: statusText(item.last_status) })
          : statusText(null)),
      ]),
      el("button", { type: "button", class: "ghost", onclick: () => showSearch(item.id) }, t("search.open")),
    ]);
  }

  // Whether SES may send to the address; in the sandbox an address has to be
  // verified first, and the page can ask SES to send the link.
  function destination(address, after) {
    if (state.production || !address) return null;
    const value = state.destinations[address] || "none";
    if (value === "verified") return el("span", { class: "badge ok" }, t("dest.verified"));
    return el("span", { class: "badge warn" }, [
      t(value === "pending" ? "dest.pending" : "dest.none"),
      el("button", {
        type: "button",
        class: "link-button",
        onclick: async () => {
          try {
            await api("/destinations", { method: "POST", body: { address } });
            toast(t("dest.sent", { address }));
          } catch (error) {
            toast(error.message);
          }
          await reload();
          if (after) after();
        },
      }, t(value === "pending" ? "dest.resend" : "dest.verify")),
    ]);
  }

  // A search ----------------------------------------------------------------

  function showSearch(id) {
    const view = dialog("");
    const preview = el("section", { class: "section hidden" });
    const runs = el("section", { class: "section" });
    let polls = 0;
    let pollTimer = null;
    view.node.addEventListener("close", () => clearTimeout(pollTimer));

    const loadRuns = async () => {
      fill(runs, [el("h3", {}, t("runs.title")), el("p", { class: "muted" }, t("loading"))]);
      let items;
      try {
        items = (await api(searchPath(find(id), "/runs"))).items || [];
      } catch (error) {
        fill(runs, [el("h3", {}, t("runs.title")), el("p", { class: "error" }, error.message)]);
        return items;
      }
      fill(runs, [
        el("h3", {}, t("runs.title")),
        items.length ? el("ul", { class: "runs" }, items.map(runItem)) : el("p", { class: "muted" }, t("runs.empty")),
      ]);
      return items;
    };

    // After "run now" the history is read again until the new run shows up.
    const pollRuns = (before) => {
      clearTimeout(pollTimer);
      if (!view.open() || polls > 12) return;
      polls += 1;
      pollTimer = setTimeout(async () => {
        const items = await loadRuns();
        if (items && items.length && items[0].at !== before) {
          await reload();
          draw();
          return;
        }
        pollRuns(before);
      }, 10000);
    };

    const draw = () => {
      const item = find(id);
      if (!item) return view.close();
      view.title(item.name || item.query);
      view.fill([
        el("dl", { class: "facts" }, [
          mine(item) ? null : el("dt", {}, t("facts.owner")),
          mine(item) ? null : el("dd", {}, item.owner),
          el("dt", {}, t("field.query")), el("dd", {}, item.query),
          el("dt", {}, t("field.engines")), el("dd", {}, item.engines.map(engineName).join(", ")),
          el("dt", {}, t("field.market")), el("dd", {}, `${marketName(item.market)} · ${t("period." + item.period)}`),
          ...(item.in_digest ? [
            el("dt", {}, t("field.schedule")), el("dd", {}, t(item.active ? "facts.inDigest" : "facts.inDigestOff")),
          ] : [
            el("dt", {}, t("field.schedule")), el("dd", {}, `${scheduleText(item)} (${item.timezone})`),
            el("dt", {}, t("facts.next")), el("dd", {}, item.active && item.next_run ? when(item.next_run) : t("search.offShort")),
            el("dt", {}, t("field.recipient")), el("dd", {}, [item.recipient + " ", destination(item.recipient, draw)]),
          ]),
          el("dt", {}, t("facts.mode")), el("dd", {}, [
            t(item.only_new ? "facts.onlyNew" : "facts.all"),
            item.skip_empty && !item.in_digest ? " · " + t("facts.skipEmpty") : "",
            item.ai ? " · " + t("facts.ai") : "",
          ]),
          el("dt", {}, t("facts.last")), el("dd", { class: statusClass(item.last_status) }, item.last_run
            ? `${when(item.last_run)} · ${statusText(item.last_status)} · ${t("facts.newCount", { count: item.last_count || 0 })}`
            : statusText(null)),
          item.last_error ? el("dt", {}, t("facts.problems")) : null,
          item.last_error ? el("dd", { class: "status-warn" }, item.last_error) : null,
        ]),
        el("div", { class: "modal-actions" }, [
          el("div", { class: "actions start" }, [
            el("button", {
              type: "button",
              class: "primary",
              onclick: async (event) => {
                event.target.disabled = true;
                try {
                  const items = await api(searchPath(item, "/runs"));
                  const before = items.items && items.items.length ? items.items[0].at : "";
                  await api(searchPath(item, "/run"), { method: "POST" });
                  toast(t("search.started"));
                  polls = 0;
                  pollRuns(before);
                } catch (error) {
                  toast(error.message);
                }
                event.target.disabled = false;
              },
            }, t("search.runNow")),
            el("button", { type: "button", class: "ghost", onclick: () => showPreview(item, preview) }, t("search.preview")),
            el("button", { type: "button", class: "ghost", onclick: () => { view.close(); showEditor(id); } }, t("search.edit")),
          ]),
          el("div", { class: "actions" }, [
            el("button", {
              type: "button",
              class: "ghost",
              title: t("search.forgetHint"),
              onclick: async () => {
                if (!window.confirm(t("search.forgetConfirm"))) return;
                try {
                  await api(searchPath(item, "/seen"), { method: "DELETE" });
                  toast(t("search.forgotten"));
                } catch (error) {
                  toast(error.message);
                }
              },
            }, t("search.forget")),
            el("button", {
              type: "button",
              class: "danger",
              onclick: async () => {
                if (!window.confirm(t("search.deleteConfirm", { name: item.name || item.query }))) return;
                try {
                  await api(searchPath(item), { method: "DELETE" });
                  toast(t("search.deleted"));
                  view.close();
                } catch (error) {
                  toast(error.message);
                }
                await reload();
              },
            }, t("search.delete")),
          ]),
        ]),
        preview,
        runs,
      ]);
    };
    draw();
    loadRuns();
  }

  // What the engines find now, without mail and without marking anything as
  // sent. Results already sent are dimmed.
  async function showPreview(item, part) {
    part.classList.remove("hidden");
    fill(part, [el("h3", {}, t("preview.title")), el("p", { class: "muted" }, t("preview.loading"))]);
    let data;
    try {
      data = await api(searchPath(item, "/preview"), { method: "POST" });
    } catch (error) {
      fill(part, [el("h3", {}, t("preview.title")), el("p", { class: "error" }, error.message)]);
      return;
    }
    const fresh = data.results.filter((result) => !result.seen).length;
    fill(part, [
      el("h3", {}, t("preview.title")),
      el("p", { class: "muted small" }, t("preview.count", { count: data.results.length, fresh })),
      engineErrors(data.errors),
      data.results.length ? resultList(data.results) : el("p", { class: "muted" }, t("preview.none")),
    ]);
  }

  function engineErrors(errors) {
    const entries = Object.entries(errors || {});
    if (!entries.length) return null;
    return el("ul", { class: "errors" }, entries.map(([engine, text]) => el("li", {}, `${engineName(engine)}: ${text}`)));
  }

  function resultList(results) {
    return el("ol", { class: "results" }, results.map((result) => el("li", { class: result.seen ? "seen" : null }, [
      el("a", { href: result.url, target: "_blank", rel: "noopener noreferrer" }, result.title),
      result.snippet ? el("span", {}, result.snippet) : null,
      el("span", { class: "meta" }, [
        result.source, result.published ? when(result.published) : "",
        t("preview.foundBy", { engines: (result.engines || []).map(engineName).join(", ") }),
        result.seen ? t("preview.seen") : "",
      ].filter(Boolean).join(" · ")),
    ])));
  }

  function runItem(run) {
    const sent = run.sent ? t("runs.sent", { count: run.new }) : statusText(run.status);
    return el("li", {}, el("details", {}, [
      el("summary", {}, [
        el("strong", {}, when(run.at)),
        el("span", { class: statusClass(run.status) }, sent),
        el("span", { class: "muted small" }, t("runs.found", { count: run.found, fresh: run.new })),
        el("span", { class: "muted small" }, t("trigger." + run.trigger)),
      ]),
      el("div", {}, [
        run.ai ? el("p", { class: "muted small" }, t("ai." + run.ai)) : null,
        run.ai_error ? el("p", { class: "error" }, run.ai_error) : null,
        run.summary ? el("p", { class: "digest" }, run.summary) : null,
        run.send_error ? el("p", { class: "error" }, run.send_error) : null,
        engineErrors(run.errors),
        run.results && run.results.length ? resultList(run.results) : null,
      ]),
    ]));
  }

  // New search or a change of one.
  function showEditor(id) {
    const item = id ? find(id) : null;
    const me = state.me;
    const view = dialog(item && !mine(item) ? t("editor.editOther", { owner: item.owner }) : t(item ? "editor.editTitle" : "editor.newTitle"));
    const value = (key, fallback) => (item && item[key] != null ? item[key] : fallback);

    const name = el("input", { type: "text", maxlength: "100", value: value("name", ""), placeholder: t("field.nameHint") });
    const query = el("input", { type: "text", maxlength: "300", required: true, value: value("query", ""), placeholder: t("field.queryHint") });
    const chosenEngines = new Set(value("engines", me.default_engines || me.engines.map((engine) => engine.id)));
    const engineBoxes = me.engines.map((engine) => el("label", { class: "check" }, [
      el("input", {
        type: "checkbox",
        checked: chosenEngines.has(engine.id),
        onchange: (event) => (event.target.checked ? chosenEngines.add(engine.id) : chosenEngines.delete(engine.id)),
      }),
      engine.label,
      (me.proxied || []).includes(engine.id) ? el("span", { class: "badge ok" }, t("field.viaProxy")) : null,
    ]));
    const defaultMarket = me.markets.find((market) => market.startsWith(language + "-")) || me.markets[0];
    const market = el("select", {}, me.markets.map((code) => el("option", { value: code, selected: code === value("market", defaultMarket) }, marketName(code))));
    const period = el("select", {}, me.periods.map((code) => el("option", { value: code, selected: code === value("period", "any") }, t("period." + code))));
    const maxResults = el("input", { type: "number", min: "3", max: "30", required: true, value: String(value("max_results", 10)) });

    const inDigest = item ? item.in_digest : Boolean(state.digest && state.digest.combined && state.view === "mine");
    const delivery = deliveryFields(item, item ? item.owner : me.email);
    const onlyNew = el("input", { type: "checkbox", checked: value("only_new", true) });
    const active = el("input", { type: "checkbox", checked: value("active", true) });
    const ai = el("input", { type: "checkbox", checked: value("ai", false), disabled: !me.ai.enabled });
    const instructions = el("textarea", { maxlength: "1000", rows: "3", placeholder: t("field.aiHint"), disabled: !me.ai.enabled }, value("ai_instructions", ""));
    const error = el("p", { class: "error", role: "alert" });
    const button = el("button", { class: "primary", type: "submit" }, t(item ? "editor.save" : "editor.create"));

    view.fill(el("form", {
      class: "fields",
      onsubmit: async (event) => {
        event.preventDefault();
        error.textContent = "";
        if (!chosenEngines.size) {
          error.textContent = t("api.bad_engines");
          return;
        }
        const delivered = inDigest ? {} : delivery.read();
        if (delivered.error) {
          error.textContent = delivered.error;
          return;
        }
        const body = {
          name: name.value.trim(),
          query: query.value.trim(),
          engines: me.engines.map((engine) => engine.id).filter((engine) => chosenEngines.has(engine)),
          market: market.value,
          period: period.value,
          max_results: Number(maxResults.value),
          ...delivered,
          only_new: onlyNew.checked,
          active: active.checked,
        };
        if (me.ai.enabled) {
          body.ai = ai.checked;
          body.ai_instructions = instructions.value.trim();
        }
        button.disabled = true;
        try {
          const saved = item
            ? await api(searchPath(item), { method: "PATCH", body })
            : await api("/searches", { method: "POST", body });
          // A new search is the user's own; the list shows it.
          if (!item && state.view !== "mine" && state.view !== "all") state.view = "mine";
          toast(t(item ? "editor.saved" : "editor.created"));
          view.close();
          await reload();
          showSearch(saved.id);
        } catch (failure) {
          error.textContent = failure.message;
          button.disabled = false;
        }
      },
    }, [
      el("label", { class: "wide" }, [t("field.query"), query]),
      el("label", { class: "wide" }, [t("field.name"), name]),
      el("fieldset", { class: "group wide" }, [el("legend", {}, t("field.engines")), ...engineBoxes]),
      el("p", { class: "hint wide" }, t("field.enginesHint")),
      el("label", {}, [t("field.market"), market]),
      el("label", {}, [t("field.period"), period]),
      el("label", {}, [t("field.maxResults"), maxResults]),
      ...(inDigest ? [el("p", { class: "note wide" }, t("editor.inDigest"))] : delivery.nodes),
      el("label", { class: "check wide" }, [onlyNew, t("field.onlyNew")]),
      el("label", { class: "check wide" }, [active, t(inDigest ? "field.activeDigest" : "field.active")]),
      el("label", { class: "check wide" }, [ai, t(me.ai.enabled ? "field.ai" : "field.aiOff")]),
      el("label", { class: "wide" }, [t("field.aiInstructions"), instructions]),
      el("p", { class: "hint wide" }, t("field.aiCost", { spent: money(me.ai.spent), limit: money(me.ai.limit) })),
      el("div", { class: "actions wide" }, [button]),
      error,
    ]));
    query.focus();
  }

  // When and where mail goes: the schedule (every N hours, or chosen weekdays
  // at an hour), the time zone, the address, the language, and whether a
  // message without news is skipped. A search and the combined digest share
  // these fields.
  function deliveryFields(source, owner) {
    const me = state.me;
    const value = (key, fallback) => (source && source[key] != null ? source[key] : fallback);
    const schedule = value("schedule", { type: "days", days: ALL_DAYS, hour: 8 });
    const kind = el("select", { onchange: () => drawSchedule() }, [
      el("option", { value: "days", selected: schedule.type === "days" }, t("schedule.byDays")),
      el("option", { value: "hours", selected: schedule.type === "hours" }, t("schedule.byHours")),
    ]);
    const every = el("select", { "aria-label": t("schedule.every") }, me.every_hours.map((hours) => el("option", { value: String(hours), selected: schedule.type === "hours" && schedule.every === hours }, t("schedule.everyHours", { count: hours }))));
    const hour = el("select", { "aria-label": t("schedule.hour") }, Array.from({ length: 24 }, (_, index) => el("option", { value: String(index), selected: (schedule.hour ?? 8) === index }, hourName(index))));
    const days = new Set(schedule.days || ALL_DAYS);
    const dayButtons = el("span", { class: "days" });
    const drawDays = () => fill(dayButtons, ALL_DAYS.map((day) => el("button", {
      type: "button",
      class: days.has(day) ? "day on" : "day",
      "aria-pressed": String(days.has(day)),
      onclick: () => {
        if (days.has(day)) days.delete(day);
        else days.add(day);
        drawDays();
      },
    }, dayName(day))));
    const scheduleBox = el("div", { class: "schedule" });
    const drawSchedule = () => {
      drawDays();
      fill(scheduleBox, kind.value === "hours"
        ? [kind, every]
        : [kind, dayButtons, el("span", {}, t("schedule.at")), hour]);
    };
    drawSchedule();

    const zones = timeZones();
    const zoneValue = value("timezone", browserZone());
    const zone = zones.length
      ? el("select", {}, (zones.includes(zoneValue) ? zones : [zoneValue, ...zones]).map((code) => el("option", { value: code, selected: code === zoneValue }, code)))
      : el("input", { type: "text", value: zoneValue });
    const recipient = el("input", { type: "email", value: value("recipient", ""), placeholder: owner, autocomplete: "email" });
    const mailLanguage = el("select", {}, me.languages.map((code) => el("option", { value: code, selected: code === value("language", me.languages.includes(language) ? language : me.languages[0]) }, (i18n.languages.find(([lang]) => lang === code) || [code, code])[1])));
    const skipEmpty = el("input", { type: "checkbox", checked: value("skip_empty", true) });
    return {
      nodes: [
        el("label", {}, [t("field.timezone"), zone]),
        el("div", { class: "wide" }, el("label", {}, [t("field.schedule"), scheduleBox])),
        el("label", {}, [el("span", {}, [t("field.recipient") + " ", source ? destination(source.recipient) : null]), recipient]),
        el("label", {}, [t("field.language"), mailLanguage]),
        el("label", { class: "check wide" }, [skipEmpty, t("field.skipEmpty")]),
      ],
      read: () => {
        if (kind.value === "days" && !days.size) return { error: t("editor.noDays") };
        return {
          schedule: kind.value === "hours"
            ? { type: "hours", every: Number(every.value) }
            : { type: "days", days: ALL_DAYS.filter((day) => days.has(day)), hour: Number(hour.value) },
          timezone: zone.value.trim(),
          recipient: recipient.value.trim(),
          language: mailLanguage.value,
          skip_empty: skipEmpty.checked,
        };
      },
    };
  }

  // The API path of the combined digest of the user whose searches are
  // shown.
  function digestPath(rest) {
    const path = "/digest" + (rest || "");
    return state.digest && state.digest.owner !== state.me.email ? path + "?owner=" + encodeURIComponent(state.digest.owner) : path;
  }

  // The combined digest: off, every search mails on its own; on, all active
  // searches of the user go out in one message on the digest's schedule.
  function digestSection() {
    const digest = state.digest;
    if (!digest) return null;
    const toggle = el("input", {
      type: "checkbox",
      checked: digest.combined,
      "aria-label": t(digest.combined ? "digest.turnOff" : "digest.turnOn"),
      onchange: async (event) => {
        toggle.disabled = true;
        try {
          await api(digestPath(), { method: "PUT", body: { combined: event.target.checked } });
          toast(t(event.target.checked ? "digest.turnedOn" : "digest.turnedOff"));
        } catch (error) {
          toast(error.message);
        }
        await reload();
      },
    });
    const facts = digest.combined ? el("dl", { class: "facts" }, [
      el("dt", {}, t("field.schedule")), el("dd", {}, `${scheduleText(digest)} (${digest.timezone})`),
      el("dt", {}, t("facts.next")), el("dd", {}, digest.next_run ? when(digest.next_run) : "—"),
      el("dt", {}, t("field.recipient")), el("dd", {}, [digest.recipient + " ", destination(digest.recipient, render)]),
      el("dt", {}, t("facts.last")), el("dd", { class: statusClass(digest.last_status) }, digest.last_run
        ? `${when(digest.last_run)} · ${statusText(digest.last_status)} · ${t("facts.newCount", { count: digest.last_count || 0 })}`
        : statusText(null)),
      digest.last_error ? el("dt", {}, t("facts.problems")) : null,
      digest.last_error ? el("dd", { class: "status-warn" }, digest.last_error) : null,
    ]) : null;
    return el("section", { class: "card digest-card" }, [
      el("div", { class: "list-head" }, [
        el("h2", {}, t("digest.title")),
        el("label", { class: "switch" }, [toggle, t(digest.combined ? "digest.on" : "digest.off")]),
      ]),
      el("p", { class: "muted small" }, t(digest.combined ? "digest.onText" : "digest.offText")),
      facts,
      digest.combined ? el("div", { class: "actions start" }, [
        el("button", { type: "button", class: "ghost", onclick: showDigestEditor }, t("digest.edit")),
        el("button", {
          type: "button",
          class: "primary",
          onclick: async (event) => {
            event.target.disabled = true;
            try {
              await api(digestPath("/run"), { method: "POST" });
              toast(t("digest.started"));
            } catch (error) {
              toast(error.message);
            }
            event.target.disabled = false;
          },
        }, t("digest.runNow")),
      ]) : null,
    ]);
  }

  function showDigestEditor() {
    const digest = state.digest;
    const view = dialog(t("digest.editTitle"));
    const delivery = deliveryFields(digest, digest.owner);
    const error = el("p", { class: "error", role: "alert" });
    const button = el("button", { class: "primary", type: "submit" }, t("editor.save"));
    view.fill(el("form", {
      class: "fields",
      onsubmit: async (event) => {
        event.preventDefault();
        const body = delivery.read();
        if (body.error) {
          error.textContent = body.error;
          return;
        }
        button.disabled = true;
        try {
          await api(digestPath(), { method: "PUT", body });
          toast(t("editor.saved"));
          view.close();
          await reload();
        } catch (failure) {
          error.textContent = failure.message;
          button.disabled = false;
        }
      },
    }, [
      el("p", { class: "muted small wide" }, t("digest.editHint")),
      ...delivery.nodes,
      el("div", { class: "actions wide" }, [button]),
      error,
    ]));
  }

  function money(value) {
    return new Intl.NumberFormat(language, { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value || 0);
  }

  // Settings ---------------------------------------------------------------

  function showAccount(section) {
    // Visitors of the demo share one account: none may change its password or
    // lock it behind their own authenticator app.
    const sections = config.demo ? [] : [["password", t("password.title")], ["mfa", t("mfa.title")]];
    sections.push(["language", t("language.title")], ["ai", t("aiTab.title")]);
    if (state.me.admin) sections.push(["users", t("users.title")], ["proxy", t("proxy.title")]);
    sections.push(["version", t("version.title")]);
    let current = sections.some(([key]) => key === section) ? section : "password";
    const body = el("div", { class: "settings-body" });
    const tabs = el("div", { class: "tabs", role: "tablist" });
    const close = () => {
      wrap.remove();
      document.removeEventListener("keydown", onKey);
    };
    const onKey = (event) => {
      if (event.key === "Escape") close();
    };
    const wrap = el("div", { class: "dialog-wrap", onclick: (event) => { if (event.target === wrap) close(); } }, [
      el("section", { class: "dialog wide", role: "dialog", "aria-modal": "true", "aria-label": t("account.title") }, [
        el("header", { class: "dialog-head" }, [
          el("h2", {}, t("account.title")),
          el("button", { type: "button", class: "icon-button", onclick: close }, t("close")),
        ]),
        tabs,
        body,
      ]),
    ]);
    function draw() {
      tabs.replaceChildren(...sections.map(([key, label]) => el("button", {
        type: "button",
        role: "tab",
        class: key === current ? "tab active" : "tab",
        "aria-selected": String(key === current),
        onclick: () => {
          current = key;
          draw();
        },
      }, label)));
      body.replaceChildren();
      if (current === "password") body.append(passwordForm());
      else if (current === "mfa") showMfa(body);
      else if (current === "ai") showAi(body);
      else if (current === "users") showUsers(body, close);
      else if (current === "proxy") showProxy(body);
      else if (current === "version") showVersion(body);
      else {
        body.append(languagePicker(async (code) => {
          await saveLanguage(code);
          // Everything on screen is redrawn in the new language.
          close();
          render();
          showAccount("language");
        }));
      }
    }
    document.querySelector(".dialog-wrap")?.remove();
    document.addEventListener("keydown", onKey);
    document.body.append(wrap);
    draw();
  }

  // Administrators: the accounts. A new user gets an invitation from Cognito;
  // the temporary password is also shown here once.
  function showUsers(part, closeSettings) {
    const note = el("div");
    const list = el("div", { class: "table-wrap" }, el("p", { class: "muted" }, t("loading")));
    const email = el("input", { type: "email", required: true, placeholder: "name@example.com", "aria-label": t("users.newEmail") });
    const admin = el("input", { type: "checkbox" });
    const invite = el("form", {
      class: "invite",
      onsubmit: async (event) => {
        event.preventDefault();
        try {
          const created = await api("/admin/users", { method: "POST", body: { email: email.value.trim(), admin: admin.checked } });
          note.replaceChildren(secretNote(created.email, created.temporary_password, true));
          email.value = "";
          admin.checked = false;
          await draw();
        } catch (error) {
          toast(error.message);
        }
      },
    }, [
      email,
      el("label", { class: "check" }, [admin, t("users.admin")]),
      el("button", { type: "submit", class: "primary" }, t("users.invite")),
    ]);
    part.replaceChildren(el("p", { class: "muted small" }, t("users.hint")), invite, note, list);

    async function update(user, changes, question) {
      if (question && !window.confirm(question)) return;
      try {
        const result = await api("/admin/users", { method: "PUT", body: { email: user.email, ...changes } });
        if (result.temporary_password) note.replaceChildren(secretNote(user.email, result.temporary_password, false));
        else toast(t("users.saved"));
      } catch (error) {
        toast(error.message);
      }
      await draw();
    }

    async function remove(user) {
      if (!window.confirm(t("users.deleteConfirm", { email: user.email, count: user.searches }))) return;
      try {
        await api("/admin/users?email=" + encodeURIComponent(user.email), { method: "DELETE" });
        toast(t("users.deleted"));
      } catch (error) {
        toast(error.message);
      }
      await draw();
      if (state.view === user.email) state.view = "all";
      await reload();
    }

    async function draw() {
      try {
        state.users = (await api("/admin/users")).items || [];
      } catch (error) {
        list.replaceChildren(el("p", { class: "error" }, error.message));
        return;
      }
      list.replaceChildren(el("table", { class: "users" }, [
        el("thead", {}, el("tr", {}, [t("users.email"), t("users.status"), "2FA", t("users.searches"), t("users.admin"), ""].map((text) => el("th", {}, text)))),
        el("tbody", {}, state.users.map((user) => el("tr", { class: user.enabled ? "" : "disabled" }, [
          el("td", {}, [user.email, user.self ? el("span", { class: "muted" }, " " + t("users.you")) : null]),
          el("td", {}, user.enabled ? t("users.state." + user.status) : t("users.disabled")),
          el("td", { class: user.mfa ? "" : "muted" }, [el("span", { class: "narrow-only" }, "2FA: "), t(user.mfa ? "users.mfaOn" : "users.mfaOff")]),
          el("td", {}, el("button", {
            type: "button",
            class: "link-button",
            title: t("users.showSearches"),
            onclick: () => {
              closeSettings();
              setView(user.self ? "mine" : user.email);
            },
          }, [el("span", { class: "narrow-only" }, t("users.searches") + ": "), String(user.searches)])),
          el("td", {}, el("label", { class: "admin-toggle" }, [
            el("input", {
              type: "checkbox",
              checked: user.admin,
              disabled: user.self,
              "aria-label": t("users.adminOf", { email: user.email }),
              onchange: (event) => update(user, { admin: event.target.checked }),
            }),
            el("span", { class: "narrow-only" }, t("users.admin")),
          ])),
          el("td", { class: "row-actions" }, user.self ? null : [
            el("button", { type: "button", onclick: () => update(user, { reset_password: true }, t("users.resetPasswordConfirm", { email: user.email })) }, t("users.resetPassword")),
            user.mfa ? el("button", { type: "button", onclick: () => update(user, { reset_mfa: true }, t("users.resetMfaConfirm", { email: user.email })) }, t("users.resetMfa")) : null,
            el("button", { type: "button", onclick: () => update(user, { enabled: !user.enabled }) }, t(user.enabled ? "users.disable" : "users.enable")),
            el("button", { type: "button", class: "danger", onclick: () => remove(user) }, t("users.delete")),
          ]),
        ]))),
      ]));
    }
    draw();
    email.focus();
  }

  // Administrators: the proxies for engines that turn a program away, such
  // as DuckDuckGo. An engine is asked directly first (unless turned off), then
  // through the proxies in their order, each when the one before fails.
  // Passwords never come back from the API: a saved proxy is sent back by its
  // id, a new one as a line of the list.
  async function showProxy(part) {
    part.replaceChildren(el("p", { class: "muted" }, t("loading")));
    let settings;
    try {
      settings = await api("/admin/proxy");
    } catch (error) {
      part.replaceChildren(el("p", { class: "error" }, error.message));
      return;
    }
    const enabled = el("input", { type: "checkbox", checked: settings.enabled });
    const directFirst = el("input", { type: "checkbox", checked: settings.direct_first });
    const chosen = new Set(settings.engines);
    const boxes = state.me.engines.map((engine) => el("label", { class: "check" }, [
      el("input", {
        type: "checkbox",
        checked: chosen.has(engine.id),
        onchange: (event) => (event.target.checked ? chosen.add(engine.id) : chosen.delete(engine.id)),
      }),
      engine.label,
    ]));
    const rows = settings.proxies.slice();
    const list = el("ol", { class: "proxy-list wide" });
    const lines = el("textarea", { rows: "4", placeholder: "socks5h://user:password@proxy.example.net:1080\nproxy.example.net:3128:user:password", autocomplete: "off", spellcheck: "false" });
    const scheme = el("select", {}, ["socks5h", "socks5", "socks4", "http", "https"].map((code) => el("option", { value: code }, code)));

    async function check(proxy, place) {
      place.replaceChildren(el("span", { class: "muted" }, t("proxy.checking")));
      try {
        const found = await api("/admin/proxy/test", { method: "POST", body: { id: proxy.id } });
        place.replaceChildren(el("dl", { class: "facts" }, [
          el("dt", {}, t("proxy.directIp")), el("dd", {}, found.direct_ip || "—"),
          el("dt", {}, t("proxy.proxyIp")), el("dd", { class: found.error ? "status-bad" : "status-ok" }, found.error || found.proxy_ip),
          found.error ? null : el("dt", {}, "DuckDuckGo"),
          found.error ? null : el("dd", { class: found.engine_error ? "status-bad" : "status-ok" },
            found.engine_error || t("proxy.found", { count: found.found })),
        ]));
      } catch (error) {
        place.replaceChildren(el("span", { class: "error" }, error.message));
      }
    }

    function move(index, step) {
      const [proxy] = rows.splice(index, 1);
      rows.splice(index + step, 0, proxy);
      draw();
    }

    function draw() {
      fill(list, rows.length ? rows.map((proxy, index) => {
        const place = el("div", { class: "proxy-check" });
        return el("li", { class: "proxy-row" }, [
          el("span", { class: "proxy-url" }, proxy.url),
          el("span", { class: "proxy-actions" }, [
            el("button", { type: "button", class: "icon-button", title: t("proxy.up"), "aria-label": t("proxy.up"), disabled: index === 0, onclick: () => move(index, -1) }, "↑"),
            el("button", { type: "button", class: "icon-button", title: t("proxy.down"), "aria-label": t("proxy.down"), disabled: index === rows.length - 1, onclick: () => move(index, 1) }, "↓"),
            el("button", {
              type: "button",
              class: "ghost",
              disabled: !settings.proxies.some((saved) => saved.id === proxy.id),
              title: t("proxy.checkSaved"),
              onclick: () => check(proxy, place),
            }, t("proxy.check")),
            el("button", { type: "button", class: "danger", onclick: () => { rows.splice(index, 1); draw(); } }, t("proxy.remove")),
          ]),
          place,
        ]);
      }) : [el("li", { class: "muted" }, t("proxy.none"))]);
    }
    draw();

    const body = () => ({
      enabled: enabled.checked,
      direct_first: directFirst.checked,
      engines: state.me.engines.map((engine) => engine.id).filter((id) => chosen.has(id)),
      scheme: scheme.value,
      proxies: [
        ...rows.map((proxy) => ({ id: proxy.id })),
        ...lines.value.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => ({ url: line })),
      ],
    });
    const save = el("button", { class: "primary", type: "submit" }, t("editor.save"));
    fill(part, [
      el("p", { class: "muted small" }, t("proxy.hint")),
      el("form", {
        class: "fields",
        onsubmit: async (event) => {
          event.preventDefault();
          save.disabled = true;
          try {
            await api("/admin/proxy", { method: "PUT", body: body() });
            toast(t("proxy.saved"));
            state.me = await api("/me");
            showProxy(part);
            return;
          } catch (error) {
            toast(error.message);
          }
          save.disabled = false;
        },
      }, [
        el("label", { class: "check wide" }, [enabled, t("proxy.enabled")]),
        el("label", { class: "check wide" }, [directFirst, t("proxy.directFirst")]),
        el("fieldset", { class: "group wide" }, [el("legend", {}, t("proxy.engines")), ...boxes]),
        el("p", { class: "hint wide" }, t("proxy.order")),
        list,
        el("label", { class: "wide" }, [t("proxy.add"), lines]),
        el("label", {}, [t("proxy.scheme"), scheme]),
        el("p", { class: "hint wide" }, t("proxy.formats")),
        el("div", { class: "actions wide" }, [save]),
      ]),
    ]);
  }

  function secretNote(email, password, invited) {
    return el("div", { class: "secret-note" }, [
      el("p", {}, t(invited ? "users.invited" : "users.newPassword", { email })),
      el("p", {}, [t("users.temporaryPassword") + " ", el("code", {}, password)]),
    ]);
  }

  // The version of the site and the releases of the demo repository that came
  // after it, with their notes. The API reads them from GitHub and keeps them
  // an hour.
  async function showVersion(part) {
    part.replaceChildren(el("p", { class: "muted" }, t("loading")));
    let info;
    try {
      info = await api("/version");
    } catch (error) {
      part.replaceChildren(el("p", { class: "error" }, error.message));
      return;
    }
    const link = (href, text) => el("a", { href, target: "_blank", rel: "noopener" }, text);
    let status;
    if (info.error) status = el("p", { class: "error" }, t("version.unavailable"));
    else if (!info.repository) status = el("p", { class: "muted" }, t("version.off"));
    else if (!info.latest) status = el("p", { class: "muted" }, t("version.none"));
    else if (!info.newer.length) status = el("p", { class: "totp-status" }, [el("span", { class: "badge ok" }, t("version.current")), " ", t("version.newest")]);
    else status = el("p", { class: "totp-status" }, [el("span", { class: "badge warn" }, t("version.behind")), " ", t("version.newer", { count: info.newer.length })]);
    fill(part, [
      el("dl", { class: "version-facts" }, [
        el("dt", {}, t("version.title")),
        el("dd", {}, info.version && info.url ? link(info.url + "/tag/" + encodeURIComponent(info.version), info.version) : info.version || "—"),
        el("dt", {}, t("version.repository")),
        el("dd", {}, info.repository ? link(info.url, info.repository) : "—"),
      ]),
      status,
      info.newer.length ? el("ul", { class: "releases" }, info.newer.map((release) => el("li", {}, [
        el("strong", {}, link(release.url, release.title)),
        release.date ? el("span", { class: "muted" }, " · " + release.date) : null,
        release.notes ? el("p", { class: "release-notes" }, release.notes) : null,
      ]))) : null,
    ]);
  }

  // The model of the digest and what it has cost this month.
  async function showAi(part) {
    part.replaceChildren(el("p", { class: "muted" }, t("loading")));
    try {
      state.me = await api("/me");
    } catch (error) {
      part.replaceChildren(el("p", { class: "error" }, error.message));
      return;
    }
    const ai = state.me.ai;
    fill(part, [
      el("p", {}, [el("span", { class: ai.enabled ? "badge ok" : "badge warn" }, t(ai.enabled ? "aiTab.on" : "aiTab.off")), t("aiTab.text")]),
      el("dl", { class: "facts" }, [
        el("dt", {}, t("aiTab.model")), el("dd", { class: "mono" }, ai.model || "—"),
        el("dt", {}, t("aiTab.spent")), el("dd", {}, `${money(ai.spent)} / ${money(ai.limit)}`),
      ]),
      el("p", { class: "muted small" }, t("aiTab.hint")),
    ]);
  }

  function passwordForm() {
    const error = el("p", { class: "error", role: "alert" });
    const current = el("input", { type: "password", autocomplete: "current-password", required: true });
    const next = el("input", { type: "password", autocomplete: "new-password", required: true, minlength: "12" });
    const again = el("input", { type: "password", autocomplete: "new-password", required: true, minlength: "12" });
    const button = el("button", { class: "primary", type: "submit" }, t("password.submit"));
    const form = el("form", {
      onsubmit: async (event) => {
        event.preventDefault();
        error.textContent = "";
        if (next.value !== again.value) {
          error.textContent = t("password.mismatch");
          return;
        }
        button.disabled = true;
        try {
          const { user } = await currentUser();
          await cognito((done) => user.changePassword(current.value, next.value, done));
          form.reset();
          toast(t("password.changed"));
        } catch (failure) {
          error.textContent = authMessage(failure);
        }
        button.disabled = false;
      },
    }, [
      el("label", {}, [t("password.current"), current]),
      el("label", {}, [t("password.new"), next]),
      el("label", {}, [t("password.again"), again]),
      el("p", { class: "muted small" }, t("password.rules")),
      el("div", { class: "actions" }, [button]),
      error,
    ]);
    return form;
  }

  // Two-factor sign-in with an authenticator app (TOTP): Cognito gives a
  // secret, the app shows codes, one code confirms the pairing.
  async function showMfa(part) {
    part.replaceChildren(el("p", { class: "muted" }, t("loading")));
    let enabled;
    try {
      const { user } = await currentUser();
      const data = await cognito((done) => user.getUserData(done, { bypassCache: true }));
      enabled = (data.UserMFASettingList || []).includes("SOFTWARE_TOKEN_MFA");
    } catch (error) {
      part.replaceChildren(el("p", { class: "error" }, authMessage(error)));
      return;
    }
    if (enabled) {
      part.replaceChildren(
        el("p", {}, [el("span", { class: "badge ok" }, t("mfa.on")), t("mfa.onText")]),
        el("div", { class: "actions" }, el("button", {
          type: "button",
          class: "danger",
          onclick: async () => {
            if (!window.confirm(t("mfa.disableConfirm"))) return;
            try {
              const { user } = await currentUser();
              await cognito((done) => user.setUserMfaPreference(null, { PreferredMfa: false, Enabled: false }, done));
              toast(t("mfa.disabled"));
            } catch (error) {
              toast(authMessage(error));
            }
            showMfa(part);
          },
        }, t("mfa.disable"))));
      return;
    }
    part.replaceChildren(
      el("p", { class: "muted" }, t("mfa.offText")),
      el("div", { class: "actions" }, el("button", { type: "button", class: "primary", onclick: () => pairApp(part) }, t("mfa.enable"))));
  }

  async function pairApp(part) {
    let user;
    let secret;
    try {
      user = (await currentUser()).user;
      secret = await new Promise((resolve, reject) => user.associateSoftwareToken({
        associateSecretCode: resolve,
        onFailure: reject,
      }));
    } catch (error) {
      toast(authMessage(error));
      return;
    }
    const uri = `otpauth://totp/${encodeURIComponent(ISSUER + ":" + state.me.email)}?secret=${secret}&issuer=${encodeURIComponent(ISSUER)}`;
    const qr = window.qrcode(0, "M");
    qr.addData(uri);
    qr.make();
    const error = el("p", { class: "error", role: "alert" });
    const code = el("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", pattern: "[0-9]{6}", maxlength: "6", required: true });
    const button = el("button", { class: "primary", type: "submit" }, t("mfa.submit"));
    part.replaceChildren(el("form", {
      class: "pair",
      onsubmit: async (event) => {
        event.preventDefault();
        error.textContent = "";
        button.disabled = true;
        try {
          await new Promise((resolve, reject) => user.verifySoftwareToken(code.value.trim(), ISSUER, { onSuccess: resolve, onFailure: reject }));
          await cognito((done) => user.setUserMfaPreference(null, { PreferredMfa: true, Enabled: true }, done));
          toast(t("mfa.enabled"));
          showMfa(part);
        } catch (failure) {
          error.textContent = authMessage(failure);
          button.disabled = false;
        }
      },
    }, [
      el("p", {}, t("mfa.step1")),
      el("img", { class: "qr", src: qr.createDataURL(5, 2), alt: t("mfa.qrAlt"), width: "205", height: "205" }),
      el("p", { class: "mono secret" }, secret),
      el("label", {}, [t("mfa.step2"), code]),
      el("div", { class: "actions" }, [button]),
      error,
    ]));
    code.focus();
  }

  async function boot() {
    if (!window.AmazonCognitoIdentity || !config.userPoolId || !config.clientId) {
      root.textContent = t("noAuth");
      return;
    }
    try {
      await currentSession();
      await loadLanguage();
      state.me = await api("/me");
      if (state.me.admin) {
        state.view = storedView() || "mine";
        await loadUsers();
      }
      await load();
      render();
    } catch (error) {
      if (!document.querySelector(".login")) showLogin();
    }
  }

  setLanguage(known(storedLanguage()) ? storedLanguage() : browserLanguage());
  boot();
})();
