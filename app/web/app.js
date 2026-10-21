import { StoryEngine } from "/engine.js";
import { createUiLanguage } from "/ui-language.js";

const $ = id => document.getElementById(id);
function normalizeSearch(value) {
  // Search keys only: preserve the displayed spelling, script and saved query.
  const voiced = { "ヷ": "わ\u3099", "ヸ": "ゐ\u3099", "ヹ": "ゑ\u3099", "ヺ": "を\u3099" };
  return value.normalize("NFKC")
    .replace(/[ァ-ヶヽヾ]/g, char => String.fromCharCode(char.charCodeAt(0) - 0x60))
    .replace(/[ヷ-ヺ]/g, char => voiced[char])
    .normalize("NFC").toLowerCase().trim();
}
const store = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(`fkg-reader:${key}`)) ?? fallback; } catch { return fallback; } },
  put(key, value) { try { localStorage.setItem(`fkg-reader:${key}`, JSON.stringify(value)); return true; } catch { return false; } },
};
const ui = createUiLanguage(document, store.get("uiLanguage", "en"));
ui.initStatic();
ui.setLanguage(ui.language);
$("ui-language").value = ui.language;
function makeUI(tag, text, className) {
  const element = make(tag, undefined, className);
  if (text !== undefined) ui.text(element, text);
  return element;
}
function normalizePlayerName(value) {
  return typeof value === "string" ? Array.from(value.replace(/[\u0000-\u001f\u007f]/g, "").trim()).slice(0, 40).join("") : "";
}
let playerName = normalizePlayerName(store.get("playerName", ""));
const termIdPattern = /^[A-Za-z][A-Za-z0-9_.-]*:[A-Za-z0-9_.:-]{1,160}$/;
function normalizeTermOverrides(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).slice(0, 10000).filter(([id, text]) => termIdPattern.test(id)
    && typeof text === "string" && text.trim().length > 0 && text.length <= 120 && !/[\u0000-\u001f\u007f]/.test(text))
    .map(([id, text]) => [id, text.trim()]));
}
let termOverrides = normalizeTermOverrides(store.get("termOverrides", {}));
function messageCardText(message) {
  // Substitute only the original marker, never a character's literal 団長.
  // A callback keeps $&, $` and other replacement syntax literal in names.
  return typeof message.textTemplate === "string"
    ? message.textTemplate.replaceAll("%user%", () => playerName || "団長")
    : message.text;
}
function messageCardSource() {
  // Sidecars bind to the untouched template, not 団長 or the user's name.
  return current?.messageCard?.textTemplate ?? current?.messageCard?.text ?? "";
}
function messageCardTranslation() {
  const command = current?.script?.commands.find(command => command.op === "mess");
  return command ? translatedField(`mess:${command.line}:text`, messageCardSource()) : null;
}
function readingTranslation() {
  const translation = current?.translation;
  return translation?.targetLanguage === "zh-Hans" && translation.scriptRevision === current.revision
    ? translation : null;
}
function personalTranslation(identity, original) {
  const row = current?.personalTranslations?.entries?.[identity];
  return row && row.source === original && typeof row.template === "string" ? row : null;
}
function translationRow(identity, original, defaultOnly = false) {
  const translation = readingTranslation(), custom = !defaultOnly && personalTranslation(identity, original);
  if (custom) return { ...custom, terms: { ...custom.terms, ...translation?.terms } };
  const row = translation?.entries?.[identity];
  if (!row || row.source !== original || typeof row.template !== "string") return null;
  return { ...row, terms: translation.terms || {} };
}
function renderTranslationTemplate(template, terms) {
  let valid = true;
  // Resolve once: user names and term defaults are literal text, not templates
  // to parse again. The Japanese engine state and reading log stay untouched.
  const rendered = template.replace(/\{\{term:([A-Za-z0-9_.:-]+)\}\}|%user%/g, (token, identity) => {
    if (token === "%user%") return playerName || "団長";
    const value = terms?.[identity];
    if (typeof value !== "string") { valid = false; return ""; }
    return Object.hasOwn(termOverrides, identity) ? termOverrides[identity] : value;
  });
  return valid ? rendered : null;
}
function translatedField(identity, original) {
  const row = translationRow(identity, original);
  return row ? renderTranslationTemplate(row.template, row.terms) : null;
}
const favorites = new Set(store.get("favorites", []));
function loadReadStories() {
  const saved = store.get("readStories", null);
  if (Array.isArray(saved)) return new Set(saved.map(String));
  // Preserve the old labels once. After migration, progress is only a bookmark:
  // opening, advancing, finishing or restoring a story must not mark it as read.
  const ids = new Set();
  try {
    for (let index = 0; index < localStorage.length; index++) {
      const match = /^fkg-reader:progress:(\d+)$/.exec(localStorage.key(index) || "");
      if (match && store.get(`progress:${match[1]}`, null)) ids.add(match[1]);
    }
  } catch {}
  store.put("readStories", [...ids]);
  return ids;
}
const readStories = loadReadStories();
let status, current, engine, cards = [], total = 0, favoriteOnly = false, loading = false;
let listGeneration = 0, loadGeneration = 0, renderGeneration = 0, autoTimer, autoMode = false;
let groupGeneration = 0;
let groups = [], groupLimit = 60;
let mainParts = [], selectedPart = "";
const savedCatalog = store.get("catalog", {});
const storedCatalogViews = store.get("catalogViews", []);
const catalogViews = new Map(Array.isArray(storedCatalogViews) ? storedCatalogViews
  .filter(entry => Array.isArray(entry) && typeof entry[0] === "string" && entry[1] && typeof entry[1] === "object").slice(-100) : []);
let groupViewKey = "", cardViewKey = "", cardViewCount = 0, catalogSaveTimer;
let browseCatalog = store.get("catalogBrowse", {});
const restoringCatalog = new Map();
let muted = store.get("muted", true), textOnly = store.get("textOnly", false);
let dialogueHidden = false;
let bgm, voice, effect, lastBgm = "", lastVoice = "", lastSe = -1;
let logVoice = null, logVoiceKey = "";
const logVoiceButtons = new Map(), logVoiceIndexes = new WeakMap();
const imageCache = new Map();
const storedPortraitAdjustments = store.get("portraitAdjustments", {});
const portraitAdjustments = storedPortraitAdjustments && typeof storedPortraitAdjustments === "object" &&
  !Array.isArray(storedPortraitAdjustments) ? storedPortraitAdjustments : {};
function normalizeReadingPreferences(value) {
  const prefs = value && typeof value === "object" ? value : {};
  return {
    language: ["ja", "zh", "bilingual"].includes(prefs.language) ? prefs.language : "bilingual",
    reversed: prefs.reversed === true,
    height: typeof prefs.height === "number" && Number.isFinite(prefs.height) ? Math.round(Math.max(140, Math.min(480, prefs.height))) : null,
    opacity: typeof prefs.opacity === "number" && Number.isFinite(prefs.opacity) ? Math.round(Math.max(0, Math.min(100, prefs.opacity))) : 85,
  };
}
let readingPreferences = normalizeReadingPreferences(store.get("readingPreferences", {}));
const defaultAudioPreferences = { bgm: 26, voice: 80, se: 80 };
function normalizeAudioPreferences(value) {
  const prefs = value && typeof value === "object" ? value : {};
  return Object.fromEntries(Object.entries(defaultAudioPreferences).map(([kind, fallback]) => [kind,
    typeof prefs[kind] === "number" && Number.isFinite(prefs[kind])
      ? Math.round(Math.max(0, Math.min(100, prefs[kind]))) : fallback]));
}
let audioPreferences = normalizeAudioPreferences(store.get("audioPreferences", {}));

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function notify(message) {
  ui.text($("toast"), message);
  $("toast").hidden = false;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => { $("toast").hidden = true; }, 3800);
}
function make(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function formatCount(count, singular, plural = `${singular}s`) {
  return `${count.toLocaleString()} ${count === 1 ? singular : plural}`;
}

function readingLanguages() {
  if (readingPreferences.language === "ja") return ["ja"];
  if (readingPreferences.language === "zh") return ["zh"];
  // Keep an untranslated story readable without changing the saved preference.
  if (!readingTranslation() && !Object.keys(current?.personalTranslations?.entries || {}).length) return ["ja"];
  return readingPreferences.reversed ? ["zh", "ja"] : ["ja", "zh"];
}
function textColumns(original, translated) {
  return readingLanguages().map(language => ({ language,
    text: language === "ja" ? original : translated ?? ui.t("No Chinese translation available") }));
}
function defaultDialogueHeight() {
  const narrow = globalThis.matchMedia?.("(max-width: 720px)")?.matches;
  return readingLanguages().length === 2
    ? (narrow ? (textOnly ? 300 : 268) : (textOnly ? 270 : 230))
    : (narrow ? (textOnly ? 236 : 198) : (textOnly ? 230 : 180));
}
function syncReadingSettings() {
  $("reading-language").value = readingPreferences.language;
  ui.text($("reading-language-order"), readingPreferences.reversed ? "Chinese ｜ Japanese" : "Japanese ｜ Chinese");
  $("swap-reading-languages").disabled = readingPreferences.language !== "bilingual";
  const height = readingPreferences.height ?? defaultDialogueHeight();
  $("dialogue-height").value = height;
  $("dialogue-height-number").value = String(height);
  ui.text($("dialogue-height-value"), `px${readingPreferences.height === null ? " · Auto" : ""}`);
  $("stage-shell").classList.toggle("custom-dialogue-height", readingPreferences.height !== null);
  $("stage-shell").style.setProperty("--custom-dialogue-height", `${height}px`);
  $("dialogue-opacity").value = readingPreferences.opacity;
  $("dialogue-opacity-number").value = String(readingPreferences.opacity);
  $("dialogue-opacity-value").textContent = "%";
  $("stage-shell").style.setProperty("--dialogue-opacity", String(readingPreferences.opacity / 100));
  syncAudioSettings();
}
function syncAudioSettings() {
  for (const [kind, level] of Object.entries(audioPreferences)) {
    $(`${kind}-volume`).value = level;
    $(`${kind}-volume-number`).value = String(level);
    $(`${kind}-volume-value`).textContent = "%";
  }
}
function updateAudioPreferences(kind, level) {
  audioPreferences = normalizeAudioPreferences({ ...audioPreferences, [kind]: level });
  const saved = store.put("audioPreferences", audioPreferences);
  // Update active players in place so moving a slider never restarts audio.
  for (const [channel, sound] of [["bgm", bgm], ["voice", voice], ["voice", logVoice], ["se", effect]]) {
    if (sound) sound.volume = audioPreferences[channel] / 100;
  }
  syncAudioSettings();
  if (!saved) notify("Volume applied, but this browser could not save it.");
}
function refreshReadingDisplay() {
  syncReadingSettings();
  if (current && engine) {
    if (current.story.resourceKind === "messagecard") renderMessageCardText();
    else renderDialogueText(engine.state);
  }
  if ($("log-dialog").open) {
    const top = $("log-list").scrollTop;
    renderLog();
    $("log-list").scrollTop = top;
  }
}
function updateReadingPreferences(patch) {
  readingPreferences = normalizeReadingPreferences({ ...readingPreferences, ...patch });
  const saved = store.put("readingPreferences", readingPreferences);
  if ("language" in patch || "reversed" in patch) refreshReadingDisplay();
  else syncReadingSettings();
  if (!saved) notify("Settings applied, but this browser could not save them.");
}
function showReadingSettings() {
  setAuto(false);
  syncReadingSettings();
  if (!$("reading-settings-dialog").open) $("reading-settings-dialog").showModal();
}

let glossaryTerms = [], glossarySelection = "", glossaryLimit = 80;
let glossaryType = "characters", glossaryKanaRow = "all", glossaryKanaInitial = "all", glossaryListKey = "";
const glossaryKanaRows = ["あいうえお", "かきくけこ", "さしすせそ", "たちつてと", "なにぬねの", "はひふへほ", "まみむめも", "やゆよ", "らりるれろ", "わゐゑをん"];
const glossaryNameCollator = new Intl.Collator("ja", { numeric: true, sensitivity: "variant" });
function glossaryIsCharacter(row) {
  return ["character", "person", "name", "nickname"].includes(row.category) || /^(?:character|person):/.test(row.id);
}
function glossaryInitial(row) {
  const name = normalizeSearch(row.jaForms?.[0] || "").normalize("NFD").replace(/[\u3099\u309a]/g, "");
  const small = { "ぁ":"あ", "ぃ":"い", "ぅ":"う", "ぇ":"え", "ぉ":"お", "っ":"つ", "ゃ":"や", "ゅ":"ゆ", "ょ":"よ", "ゎ":"わ", "ゕ":"か", "ゖ":"け" };
  const first = small[name[0]] || name[0];
  return glossaryKanaRows.some(kana => kana.includes(first)) ? first : "other";
}
function glossaryRowKey(row) {
  const initial = glossaryInitial(row);
  return glossaryKanaRows.find(kana => kana.includes(initial))?.[0] || "other";
}
function glossarySort(a, b) {
  const rank = row => { const index = glossaryKanaRows.findIndex(kana => kana.includes(glossaryInitial(row))); return index < 0 ? glossaryKanaRows.length : index; };
  return rank(a) - rank(b) || glossaryNameCollator.compare(normalizeSearch(a.jaForms[0] || a.id), normalizeSearch(b.jaForms[0] || b.id)) || a.id.localeCompare(b.id);
}
function renderGlossaryKana(rows) {
  $("glossary-kana-filters").hidden = glossaryType !== "characters";
  const groups = $("glossary-kana-rows"), initials = $("glossary-kana-initials");
  groups.replaceChildren(); initials.replaceChildren();
  if (glossaryType !== "characters") return;
  const add = (parent, key, label, active, count, select) => {
    const button = makeUI("button", label); button.type = "button"; button.dataset.kana = key;
    button.setAttribute("aria-pressed", String(active)); button.disabled = !count && !active && key !== "all";
    ui.attr(button, "title", `${label} · ${formatCount(count, "term")}`);
    button.addEventListener("click", () => { select(key); glossaryLimit = 80; renderGlossary();
      [...parent.children].find(child => child.dataset.kana === key)?.focus({ preventScroll: true }); });
    parent.append(button);
  };
  const changeRow = key => { glossaryKanaRow = key; glossaryKanaInitial = "all"; };
  add(groups, "all", "All", glossaryKanaRow === "all", rows.length, changeRow);
  for (const key of [...glossaryKanaRows.map(kana => kana[0]), "other"]) {
    add(groups, key, key === "other" ? "Other" : key, glossaryKanaRow === key,
      rows.filter(row => glossaryRowKey(row) === key).length, changeRow);
  }
  const kana = glossaryKanaRows.find(group => group[0] === glossaryKanaRow);
  initials.hidden = !kana;
  if (kana) {
    const groupRows = rows.filter(row => glossaryRowKey(row) === glossaryKanaRow), changeInitial = key => { glossaryKanaInitial = key; };
    add(initials, "all", "All", glossaryKanaInitial === "all", groupRows.length, changeInitial);
    for (const key of kana) add(initials, key, key, glossaryKanaInitial === key, groupRows.filter(row => glossaryInitial(row) === key).length, changeInitial);
  }
}
function glossaryRows() {
  const rows = new Map(glossaryTerms.map(row => [row.id, row]));
  const translation = readingTranslation();
  const terms = Object.assign({}, ...Object.values(current?.personalTranslations?.entries || {}).map(row => row.terms), translation?.terms);
  for (const [id, zhDefault] of Object.entries(terms)) {
    const info = translation?.termInfo?.[id] || rows.get(id);
    // A whole-field token has an exact original label; do not infer a label
    // from a substring in a sentence or merge terms sharing a Chinese word.
    const exact = Object.values(translation?.entries || {}).find(row => row.template === `{{term:${id}}}`)?.source;
    rows.set(id, { id, zhDefault, category: info?.category || (id.startsWith("local:") ? "local" : "other"),
      jaForms: info?.jaForms?.length ? info.jaForms : exact ? [exact] : [],
      characterIcon: info?.characterIcon || rows.get(id)?.characterIcon,
      characterPortrait: info?.characterPortrait || rows.get(id)?.characterPortrait });
  }
  for (const id of Object.keys(termOverrides)) {
    if (!rows.has(id)) rows.set(id, { id, zhDefault: "", category: "other", jaForms: [] });
  }
  return [...rows.values()];
}
function glossaryCharacterAvatar(row) {
  const icon = row?.characterIcon;
  if (row?.category !== "character" || !row.id.startsWith("character:")) return null;
  if (!localIconUrl(icon)) return characterAvatar(null, row.characterPortrait, "glossary-character-avatar");
  const box = make("span", undefined, "glossary-character-avatar"), image = make("img");
  box.setAttribute("aria-hidden", "true");
  image.src = icon.url; image.alt = ""; image.loading = "lazy"; image.decoding = "async";
  image.width = 100; image.height = 100;
  image.addEventListener("error", () => { image.hidden = true; box.classList.toggle("unavailable", true); });
  box.append(image); return box;
}
function renderGlossary() {
  const query = normalizeSearch($("glossary-search").value), scope = $("glossary-scope").value;
  const used = Object.assign({}, ...Object.values(current?.personalTranslations?.entries || {}).map(row => row.terms), readingTranslation()?.terms);
  const matches = glossaryRows().filter(row => (scope !== "story" || Object.hasOwn(used, row.id))
    && (scope !== "custom" || Object.hasOwn(termOverrides, row.id))
    && normalizeSearch([row.id, ...row.jaForms, row.zhDefault, termOverrides[row.id] || ""].join(" ")).includes(query));
  const characters = matches.filter(glossaryIsCharacter), other = matches.filter(row => !glossaryIsCharacter(row));
  for (const [type, count] of [["characters", characters.length], ["other", other.length]]) {
    const button = $(`glossary-${type}`); ui.text(button, `${type === "characters" ? "Characters" : "Other terms"} · ${count}`);
    button.setAttribute("aria-pressed", String(glossaryType === type));
  }
  renderGlossaryKana(characters);
  const rows = (glossaryType === "characters" ? characters.filter(row => (glossaryKanaRow === "all" || glossaryRowKey(row) === glossaryKanaRow)
    && (glossaryKanaInitial === "all" || glossaryInitial(row) === glossaryKanaInitial)) : other).sort(glossarySort);
  if (!rows.some(row => row.id === glossarySelection)) glossarySelection = rows[0]?.id || "";
  const list = $("glossary-list"), listKey = JSON.stringify([query, scope, glossaryType, glossaryKanaRow, glossaryKanaInitial]);
  const top = listKey === glossaryListKey ? list.scrollTop : 0; glossaryListKey = listKey; list.replaceChildren();
  for (const row of rows.slice(0, glossaryLimit)) {
    const button = make("button", undefined, "glossary-term"); button.type = "button";
    button.setAttribute("aria-pressed", String(row.id === glossarySelection));
    const avatar = glossaryCharacterAvatar(row), copy = make("span", undefined, "glossary-term-copy");
    if (avatar) button.append(avatar);
    const name = make("span", row.jaForms.join(" / ") || row.id); name.lang = "ja";
    const value = make("small", termOverrides[row.id] || row.zhDefault || ui.t("Default unavailable")); value.lang = "zh-Hans";
    copy.append(name, value); button.append(copy);
    if (Object.hasOwn(termOverrides, row.id)) copy.append(makeUI("small", "Customized", "glossary-custom-badge"));
    button.dataset.term = row.id;
    button.addEventListener("click", () => { glossarySelection = row.id; renderGlossary();
      [...list.children].find(child => child.dataset.term === row.id)?.focus({ preventScroll: true }); });
    list.append(button);
  }
  if (rows.length > glossaryLimit) {
    const more = makeUI("button", "Load more", "small-button");
    more.addEventListener("click", () => { glossaryLimit += 80; renderGlossary(); }); list.append(more);
  }
  if (!rows.length) list.append(makeUI("p", "No matching terms.", "muted"));
  list.scrollTop = top;
  ui.text($("glossary-count"), `${rows.length} terms · ${Object.keys(termOverrides).length} customized`);
  const selected = rows.find(row => row.id === glossarySelection);
  $("glossary-fields").hidden = !selected;
  if (selected?.jaForms?.length) ui.plain($("glossary-term-name"), selected.jaForms.join(" / "));
  else ui.text($("glossary-term-name"), selected ? "Translation term" : "Choose a term");
  $("glossary-term-id").textContent = selected?.id || "";
  const selectedAvatar = glossaryCharacterAvatar(selected), avatarBox = $("glossary-term-avatar");
  avatarBox.replaceChildren(...(selectedAvatar ? [selectedAvatar] : [])); avatarBox.hidden = !selectedAvatar;
  ui.text($("glossary-save-status"), "");
  if (selected) {
    if (selected.zhDefault) ui.plain($("glossary-default"), selected.zhDefault);
    else ui.text($("glossary-default"), "Default unavailable until this story is opened");
    $("glossary-custom").value = termOverrides[selected.id] || "";
    ui.text($("glossary-term-scope"), selected.id.startsWith("local:")
      ? "Story-specific term. Other term IDs are not changed."
      : "Shared term. Applies wherever this term ID is used.");
    $("glossary-reset").disabled = !Object.hasOwn(termOverrides, selected.id);
  }
}
async function showGlossary() {
  setAuto(false); glossaryLimit = 80;
  glossaryType = "characters"; glossaryKanaRow = glossaryKanaInitial = "all"; glossaryListKey = "";
  $("glossary-search").value = "";
  $("glossary-scope").value = readingTranslation() ? "story" : "all";
  renderGlossary(); if (!$("glossary-dialog").open) $("glossary-dialog").showModal();
  try {
    const result = await api("/api/translation-terms");
    glossaryTerms = (Array.isArray(result.items) ? result.items : []).filter(row => row && termIdPattern.test(row.id)
      && Array.isArray(row.jaForms) && row.jaForms.every(form => typeof form === "string") && typeof row.zhDefault === "string");
    if ($("glossary-dialog").open) renderGlossary();
  } catch { ui.text($("glossary-save-status"), "Global glossary unavailable. Current-story terms can still be edited."); }
}
function saveTermOverride(id, value) {
  if (!termIdPattern.test(id) || !glossaryRows().some(row => row.id === id) || typeof value !== "string"
      || value.length > 120 || /[\u0000-\u001f\u007f]/.test(value)) {
    ui.text($("glossary-save-status"), "Use up to 120 characters on one line."); return false;
  }
  const next = { ...termOverrides };
  if (value.trim()) next[id] = value.trim(); else delete next[id];
  if (!store.put("termOverrides", next)) {
    ui.text($("glossary-save-status"), "Not saved: browser storage is unavailable."); return false;
  }
  termOverrides = next;
  const scrolls = ["message", "message-translation", "log-list"].map(key => [key, $(key).scrollTop]);
  refreshReadingDisplay();
  for (const [key, top] of scrolls) $(key).scrollTop = top;
  renderGlossary();
  ui.text($("glossary-save-status"), value.trim() ? "Saved. Translation updated." : "Default restored.");
  return true;
}

let translationEdit = null, revisionImport = null, revisionImportBusy = false;
function scriptField(identity) {
  for (const command of current?.script?.commands || []) {
    if (command.op === "mess") {
      if (identity === `mess:${command.line}:text`) return current?.messageCard ? messageCardSource() : command.text || "";
      if (identity === `mess:${command.line}:name`) return command.name || "";
    } else if (command.op === "choice") {
      const item = command.data?.list?.find((item, index) => identity === StoryEngine.choiceFieldId(command, index));
      if (item) return item.text;
    }
  }
  return null;
}
function canRevise(identity, original) {
  return current && (!current.messageCard || !!messageCardSource().trim()) && typeof original === "string"
    && !!original.trim() && scriptField(identity) === original;
}
function revisionTerms() {
  return Object.assign({}, ...Object.values(current?.personalTranslations?.entries || {}).map(row => row.terms),
    readingTranslation()?.terms);
}
function editIsDirty() {
  return translationEdit && $("translation-edit-text").value !== translationEdit.initialTemplate;
}
function previewTranslationEdit() {
  if (!translationEdit) return;
  const text = $("translation-edit-text").value;
  const stripped = text.replace(/\{\{term:([A-Za-z0-9_.:-]+)\}\}/g, "");
  const preview = renderTranslationTemplate(text, translationEdit.terms);
  if (preview === null || stripped.includes("{{") || stripped.includes("}}")) ui.text($("translation-edit-preview"),
    "Unknown or incomplete glossary link. Repair the link before saving.");
  else ui.plain($("translation-edit-preview"), preview);
  $("translation-unlink").disabled = translationEdit.busy || !/\{\{term:/.test(text);
}
function fillTranslationEditor(identity) {
  const edit = translationEdit, source = scriptField(identity);
  if (!edit || !canRevise(identity, source)) return;
  edit.identity = identity; edit.source = source;
  $("translation-edit-location").textContent = `${current.story.displayTitle || current.story.title} · ${identity}`;
  edit.terms = revisionTerms();
  const custom = personalTranslation(identity, source), row = translationRow(identity, source);
  edit.expectedUpdatedAt = current.personalTranslations?.versions?.[identity] ?? custom?.updatedAt ?? null;
  edit.initialTemplate = row?.template ?? "";
  $("translation-edit-field").value = identity;
  $("translation-edit-source").textContent = source.replaceAll("\\n", "\n");
  $("translation-edit-text").value = edit.initialTemplate;
  $("translation-edit-reset").disabled = !custom;
  ui.text($("translation-edit-status"), custom ? "Personal revision loaded." : "Original translation is unchanged until you save a revision.");
  $("translation-discard").hidden = true;
  const terms = $("translation-edit-terms"); terms.replaceChildren();
  // Show exact IDs, not guessed replacements for words sharing one spelling.
  for (const [id, fallback] of Object.entries(edit.terms)) {
    const button = make("button", termOverrides[id] || fallback); button.type = "button"; button.title = id;
    button.addEventListener("click", () => {
      if (edit.busy) return;
      const input = $("translation-edit-text"), start = input.selectionStart ?? input.value.length, end = input.selectionEnd ?? start;
      const token = `{{term:${id}}}`;
      input.value = input.value.slice(0, start) + token + input.value.slice(end);
      input.focus(); input.setSelectionRange?.(start + token.length, start + token.length);
      previewTranslationEdit();
    });
    terms.append(button);
  }
  previewTranslationEdit();
}
function showTranslationEditor(identity, source) {
  if (!canRevise(identity, source)) return;
  setAuto(false);
  translationEdit = { storyId: String(current.story.id), busy: false };
  $("translation-edit-location").textContent = `${current.story.displayTitle || current.story.title} · ${identity}`;
  const picker = $("translation-edit-field"); picker.replaceChildren();
  const match = /^mess:(\d+):/.exec(identity), choices = match
    ? [[`mess:${match[1]}:text`, current.messageCard ? "Card text" : "Dialogue"],
      [`mess:${match[1]}:name`, current.messageCard ? "Sender" : "Speaker"]] : [[identity, "Selected response"]];
  for (const [key, label] of choices) {
    if (!canRevise(key, scriptField(key))) continue;
    const option = makeUI("option", label); option.value = key; picker.append(option);
  }
  $("translation-field-row").hidden = picker.children.length < 2;
  fillTranslationEditor(identity);
  if (!$("translation-editor").open) $("translation-editor").showModal();
}
function closeTranslationEditor(discard = false) {
  if (translationEdit?.busy) return;
  if (!discard && editIsDirty()) {
    ui.text($("translation-edit-status"), "You have unsaved changes. Save the revision, or choose Discard changes.");
    $("translation-discard").hidden = false; return;
  }
  $("translation-editor").close(); translationEdit = null;
}
function refreshPersonalDisplay() {
  const scrolls = ["message", "message-translation", "messagecard-view", "log-list"].map(id => [id, $(id).scrollTop]);
  refreshReadingDisplay();
  for (const [id, top] of scrolls) $(id).scrollTop = top;
}
async function saveTranslationEdit(reset = false) {
  const edit = translationEdit;
  if (!edit || edit.busy || String(current?.story.id) !== edit.storyId) return;
  const template = $("translation-edit-text").value;
  if (!reset && (!template.trim() || template.length > 30000)) {
    ui.text($("translation-edit-status"), "Enter 1–30,000 characters. Use Use default to remove a revision."); return;
  }
  edit.busy = true;
  for (const id of ["translation-edit-save", "translation-edit-reset", "translation-edit-field", "translation-editor-close", "translation-unlink", "translation-discard"]) $(id).disabled = true;
  $("translation-edit-text").readOnly = true;
  ui.text($("translation-edit-status"), "Saving…");
  try {
    const usedTerms = [...template.matchAll(/\{\{term:([A-Za-z0-9_.:-]+)\}\}/g)].map(match => match[1]);
    const terms = Object.fromEntries(usedTerms.filter(id => typeof edit.terms[id] === "string").map(id => [id, edit.terms[id]]));
    const result = await api("/api/personal-translations/save", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ storyId: edit.storyId, identity: edit.identity, source: edit.source,
        template, terms, expectedUpdatedAt: edit.expectedUpdatedAt, reset }) });
    if (String(current?.story.id) === edit.storyId) { current.personalTranslations = result; refreshPersonalDisplay(); }
    fillTranslationEditor(edit.identity);
    ui.text($("translation-edit-status"), reset ? "Default restored. Original translation was not changed." : "Saved locally. Dialogue and History updated.");
  } catch (error) { ui.text($("translation-edit-status"), `Not saved: ${error.message}`); }
  finally {
    edit.busy = false;
    for (const id of ["translation-edit-save", "translation-edit-field", "translation-editor-close", "translation-discard"]) $(id).disabled = false;
    $("translation-edit-reset").disabled = !personalTranslation(edit.identity, edit.source);
    $("translation-edit-text").readOnly = false;
    previewTranslationEdit();
  }
}
async function showRevisionManager() {
  setAuto(false); revisionImport = null;
  $("revision-import-options").hidden = true; ui.text($("revision-import-status"), "");
  ui.text($("revision-summary"), "Loading…");
  if (!$("revision-manager").open) $("revision-manager").showModal();
  try {
    const document = await api("/api/personal-translations");
    const rows = document.entries;
    ui.text($("revision-summary"), `${formatCount(rows.length, "revised field")} · ${formatCount(new Set(rows.map(row => row.storyId)).size, "story", "stories")}`);
    if (current?.personalTranslations?.staleCount || current?.personalTranslations?.warning) ui.text($("revision-summary"),
      `${formatCount(rows.length, "revised field")} · ${formatCount(new Set(rows.map(row => row.storyId)).size, "story", "stories")}`
      + (current.personalTranslations.staleCount ? ` · ${current.personalTranslations.staleCount} inactive in this story (original text changed)` : "")
      + (current.personalTranslations.warning ? ` · ${current.personalTranslations.warning}` : ""));
  } catch (error) { ui.text($("revision-summary"), `Revisions unavailable: ${error.message}`); }
}
async function prepareRevisionImport(file) {
  if (!file || revisionImportBusy) return;
  revisionImport = null; $("revision-import-options").hidden = true;
  try {
    if (file.size > 20 * 1024 * 1024) throw new Error("The revisions file exceeds 20 MB.");
    const document = JSON.parse((await file.text()).replace(/^\uFEFF/, ""));
    const result = await api("/api/personal-translations/import", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document, preview: true }) });
    revisionImport = document; $("revision-replace").checked = false;
    ui.text($("revision-import-preview"), `${result.total} revisions · ${result.added} new · ${result.conflicts} matching existing fields`);
    $("revision-import-options").hidden = false;
    ui.text($("revision-import-status"), "Preview only. Nothing has been imported yet.");
  } catch (error) { ui.text($("revision-import-status"), `Not imported: ${error.message}`); }
}
async function applyRevisionImport() {
  if (!revisionImport || revisionImportBusy) return;
  revisionImportBusy = true; $("revision-import-apply").disabled = $("revision-import").disabled = true;
  try {
    const result = await api("/api/personal-translations/import", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document: revisionImport, replace: $("revision-replace").checked }) });
    revisionImport = null; $("revision-import-options").hidden = true;
    // Fetch overlay metadata only; do not replay the scene or restart audio.
    if (current) {
      const id = current.story.id, payload = await api(`/api/story/${id}`);
      if (current?.story.id === id) { current.personalTranslations = payload.personalTranslations; refreshPersonalDisplay(); }
    }
    await showRevisionManager();
    ui.text($("revision-import-status"), `Imported: ${result.added} added, ${result.replaced} replaced, ${result.kept} existing revisions kept.`);
  } catch (error) { ui.text($("revision-import-status"), `Import or display refresh failed: ${error.message}. Reopen Personal revisions to check the saved file.`); }
  finally { revisionImportBusy = false; $("revision-import-apply").disabled = $("revision-import").disabled = false; }
}

function catalogSnapshot() {
  return { category: $("category").value, group: $("group").value,
    part: $("category").value === "mainstory" ? selectedPart : "",
    query: $("search").value, groupQuery: $("group-search").value, favoriteOnly };
}
function catalogGroupKey() {
  return JSON.stringify(["groups", $("category").value, selectedPart, $("group-search").value.trim()]);
}
function catalogGroupFilterKey() { return JSON.stringify(["group-filter", $("category").value, selectedPart]); }
function catalogCardKey() {
  return JSON.stringify(["stories", favoriteOnly, $("category").value, selectedPart, $("group").value, $("search").value.trim()]);
}
function catalogPosition(key) { return catalogViews.get(key) || {}; }
function rememberCatalogPanel(id, key, extra = {}) {
  const panel = $(id);
  // display:none clamps scrollTop to zero. Never overwrite a remembered parent
  // with the hidden child, or with an intermediate frame during restoration.
  if (!key || !$("library-dialog").open || panel.hidden || restoringCatalog.has(id)) return;
  catalogViews.delete(key);
  catalogViews.set(key, { top: panel.scrollTop, left: panel.scrollLeft, ...extra });
  while (catalogViews.size > 100) catalogViews.delete(catalogViews.keys().next().value);
}
function rememberCatalog() {
  rememberCatalogPanel("group-panel", groupViewKey, { limit: groupLimit });
  rememberCatalogPanel("story-list", cardViewKey, { count: cardViewCount });
  rememberCatalogPanel("category-rail", "rail");
  store.put("catalogViews", [...catalogViews]);
}
function restoreCatalogPanel(id, key) {
  const panel = $(id);
  if (!key || !$("library-dialog").open || panel.hidden) return;
  const saved = catalogPosition(key);
  const restore = () => {
    panel.scrollTop = Math.max(0, Number(saved.top) || 0);
    panel.scrollLeft = Math.max(0, Number(saved.left) || 0);
  };
  const token = {};
  restoringCatalog.set(id, token);
  restore();
  // showModal/focus and the new grid both need layout before restoring. Image
  // dimensions are reserved, so lazy-loaded banners cannot move the viewport.
  if (globalThis.requestAnimationFrame) {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (restoringCatalog.get(id) !== token) return;
      if ($("library-dialog").open && !panel.hidden) restore();
      restoringCatalog.delete(id);
    }));
  } else restoringCatalog.delete(id);
}
function restoreCatalog() {
  restoreCatalogPanel("group-panel", groupViewKey);
  restoreCatalogPanel("story-list", cardViewKey);
  restoreCatalogPanel("category-rail", "rail");
}
function saveCatalogSelection() {
  const snapshot = catalogSnapshot();
  store.put("catalog", snapshot);
  if (!favoriteOnly) { browseCatalog = snapshot; store.put("catalogBrowse", snapshot); }
  store.put("catalogViews", [...catalogViews]);
}
async function applyCatalogSelection(snapshot) {
  $("category").value = status.categoryOptions?.some(option => option.key === snapshot.category) ? snapshot.category : "";
  favoriteOnly = !!snapshot.favoriteOnly;
  $("search").value = snapshot.query || "";
  $("group-search").value = snapshot.groupQuery || "";
  selectedPart = snapshot.part || "";
  if (!(await loadGroups())) return;
  $("group").value = snapshot.category !== "datescene" && groups.some(group => group.key === snapshot.group) ? snapshot.group : "";
  selectedPart = groups.find(group => group.key === $("group").value)?.partKey || (mainParts.some(part => part.key === snapshot.part) ? snapshot.part : "");
  updateLibraryTabs(); renderGroups();
  await loadCards();
}
function setMode() {
  if (textOnly || !current || current.story.resourceKind === "messagecard") dialogueHidden = false;
  syncDialogueVisibility();
  $("stage-shell").classList.toggle("text-only", textOnly && !!current && !current.theaterMedia);
  $("stage-shell").classList.toggle("is-theater-media", !!current?.theaterMedia);
  $("stage-shell").classList.toggle("is-messagecard", current?.story.resourceKind === "messagecard");
  ui.text($("mode-button"), textOnly ? "Text only" : "Illustrated");
  ui.text($("audio-button"), muted ? "Sound off" : "Sound on");
  $("audio-button").setAttribute("aria-pressed", String(!muted));
  ui.text($("log-audio-button"), muted ? "Sound off" : "Sound on");
  $("log-audio-button").setAttribute("aria-pressed", String(!muted));
  $("portrait-setting-row").hidden = textOnly || current?.story.resourceKind === "messagecard";
  syncReadingSettings();
}

async function loadStatus() {
  status = await api("/api/status");
  $("import-button").hidden = status.portable === true;
  ui.text($("welcome-count"), `${status.stories.toLocaleString()} local stories & cards · Original Japanese`);
  ui.text($("asset-summary"), `${status.assets.expressions} expressions`);
  const oldCategory = $("category").value;
  $("category").replaceChildren(make("option", "すべてのカテゴリ"));
  $("category").firstChild.value = "";
  const categories = status.categoryOptions || status.categories.map(label => ({ key: label, label, count: status.metadata?.categories?.[label] }));
  for (const category of categories) {
    const option = make("option", category.count === undefined ? category.label : `${category.label} · ${category.count}`);
    option.value = category.key;
    $("category").append(option);
  }
  if (categories.some(category => category.key === oldCategory)) $("category").value = oldCategory;
  renderCategoryGallery(categories);
  renderCategoryPreview();
  await loadGroups();
  const info = $("source-info");
  info.replaceChildren();
  for (const [label, value] of [
    ["Story folder", status.config.storyRoot], ["Game cache", status.config.cacheRoot],
    ...(status.config.dateRoot ? [["Date scene folder", status.config.dateRoot]] : []),
    ...(status.sceneAssets?.root ? [["Backgrounds / portraits", status.sceneAssets.root]] : []),
  ]) {
    const row = make("p"); row.append(makeUI("strong", `${label}: `), make("span", value)); info.append(row);
  }
  for (const issue of status.issues) info.append(make("p", issue));
  if (status.sceneAssets?.downloading) info.append(makeUI("p", "Backgrounds and portraits are downloading. Use Rescan library when finished to load new assets."));

}

function bannerImage(asset, alt, className = "catalog-banner") {
  if (!asset?.url) return null;
  const img = make("img", undefined, className);
  img.src = asset.url; img.alt = alt; img.loading = "lazy"; img.decoding = "async";
  if (asset.size?.w && asset.size?.h) { img.width = asset.size.w; img.height = asset.size.h; }
  img.addEventListener("error", () => { img.hidden = true; });
  return img;
}

function characterIcon(asset, name = "") {
  const box = make("span", undefined, "character-icon");
  const fallback = make("span", name.trim().slice(0, 1) || "✿", "character-icon-fallback");
  ui.attr(fallback, "title", "Character icon not cached");
  fallback.setAttribute("aria-hidden", "true");
  box.append(fallback);
  if (asset?.url) {
    const img = make("img");
    img.src = asset.url; img.alt = ""; img.loading = "lazy"; img.decoding = "async";
    img.width = 100; img.height = 100;
    img.addEventListener("load", () => { fallback.hidden = true; });
    img.addEventListener("error", () => { img.hidden = true; fallback.hidden = false; });
    box.append(img);
  }
  return box;
}

function renderCategoryPreview() {
  const category = status.categoryOptions?.find(option => option.key === $("category").value);
  const selectedGroup = groups.find(group => group.key === $("group").value);
  const part = category?.key === "mainstory" ? mainParts.find(part => part.key === selectedPart) : null;
  $("catalog-title").textContent = favoriteOnly ? ui.t("Favorites") : selectedGroup?.label || part?.label || category?.label || "すべてのカテゴリ";
  $("catalog-path").textContent = selectedGroup ? [category?.label, part?.label, selectedGroup.label].filter(Boolean).join(" / ") : part ? `${category.label} / ${part.label}` : "";
  $("catalog-path").hidden = !$("catalog-path").textContent;
  $("category-all").classList.toggle("active", !category && !favoriteOnly);
  $("catalog-back").hidden = !$("group").value && !$("search").value && !favoriteOnly && !part;
  ui.text($("catalog-back"), part ? ($("group").value || $("search").value.trim() ? "← Chapters" : "← Parts") : "← Back to list");
  for (const button of $("category-gallery").children) {
    button.classList.toggle("active", button.dataset.category === $("category").value && !favoriteOnly);
    button.setAttribute("aria-pressed", String(button.dataset.category === $("category").value && !favoriteOnly));
  }
}

function renderCategoryGallery(categories) {
  rememberCatalogPanel("category-rail", "rail");
  const gallery = $("category-gallery"); gallery.replaceChildren();
  for (const category of categories) {
    const button = make("button", undefined, "category-tile");
    button.dataset.category = category.key;
    button.setAttribute("aria-label", `${category.label} · ${category.count ?? ""}`);
    const img = bannerImage(category.banner, "", "category-tile-image");
    if (img) button.append(img);
    const caption = make("span", undefined, "category-tile-caption");
    caption.append(make("span", category.label), makeUI("small", category.key === "messagecard"
      ? formatCount(category.count ?? 0, "card") : category.key === "theater" ? formatCount(category.count ?? 0, "item") : formatCount(category.count ?? 0, "story", "stories")));
    button.append(caption);
    button.addEventListener("click", async () => {
      rememberCatalog(); clearTimeout(searchTimer);
      favoriteOnly = false; updateLibraryTabs();
      $("search").value = ""; $("group-search").value = "";
      $("category").value = category.key;
      await selectCategory();
    });
    gallery.append(button);
  }
  restoreCatalogPanel("category-rail", "rail");
}

async function selectCategory() {
  rememberCatalog(); clearTimeout(searchTimer);
  favoriteOnly = false; updateLibraryTabs();
  selectedPart = "";
  $("group-search").value = catalogPosition(catalogGroupFilterKey()).query || "";
  renderCategoryPreview();
  listGeneration++; cards = []; loading = false; cardViewKey = "";
  $("story-list").replaceChildren(makeUI("p", "Loading library…", "empty-list"));
  try { if (await loadGroups(false)) await loadCards(); }
  catch (error) { notify(error.message); }
}

async function loadGroups(preserve = true) {
  rememberCatalog(); groupViewKey = "";
  const generation = ++groupGeneration;
  const category = $("category").value;
  const oldGroup = preserve && category !== "datescene" ? $("group").value : "";
  const oldPart = preserve && category === "mainstory" ? selectedPart : "";
  $("group").replaceChildren(make("option", category ? "すべての章・イベント・キャラクター" : "カテゴリを選択してください"));
  $("group").firstChild.value = "";
  $("group").disabled = true;
  groups = []; mainParts = []; selectedPart = ""; groupLimit = 60;
  if (!category || !status.categoryOptions) { renderGroups(); return true; }
  const data = await api(`/api/groups?${new URLSearchParams({ category })}`);
  if (generation !== groupGeneration || category !== $("category").value) return false;
  groups = data.items;
  if (category === "mainstory") {
    const parts = new Map();
    for (const group of groups) {
      if (!group.partKey) continue;
      if (!parts.has(group.partKey)) parts.set(group.partKey, { key: group.partKey, label: group.partLabel, shortLabel: group.partShortLabel, banner: group.partBanner, count: 0, chapterCount: 0 });
      const part = parts.get(group.partKey);
      part.count += group.count; part.chapterCount++;
    }
    mainParts = [...parts.values()];
    selectedPart = groups.find(group => group.key === oldGroup)?.partKey || (parts.has(oldPart) ? oldPart : "");
  }
  for (const group of data.items) {
    const option = make("option", `${group.label}${group.qualifier ? ` · ${group.qualifier}` : ""} · ${group.count}`);
    option.value = group.key;
    $("group").append(option);
  }
  $("group").disabled = data.items.length < 2;
  if (data.items.some(group => group.key === oldGroup)) $("group").value = oldGroup;
  renderGroups();
  return true;
}

function renderGroups() {
  rememberCatalogPanel("group-panel", groupViewKey, { limit: groupLimit });
  const key = catalogGroupKey();
  if (groupViewKey !== key) groupLimit = Math.max(60, Math.min(10000, Number(catalogPosition(key).limit) || 60));
  groupViewKey = key;
  catalogViews.set(catalogGroupFilterKey(), { query: $("group-search").value });
  const query = normalizeSearch($("group-search").value);
  const mainStory = $("category").value === "mainstory" && mainParts.length > 0;
  const browsingParts = mainStory && !selectedPart;
  const source = browsingParts ? mainParts : mainStory ? groups.filter(group => group.partKey === selectedPart) : groups;
  const filtered = source.filter(group => normalizeSearch([group.label, group.shortLabel, group.characterName,
    browsingParts ? groups.filter(chapter => chapter.partKey === group.key).map(chapter => chapter.label).join(" ") : "",
    group.characterBookNum == null ? "" : `No.${group.characterBookNum}`]
    .filter(Boolean).join(" ")).includes(query));
  const gallery = $("group-gallery"); gallery.replaceChildren();
  const characterCategory = ["characterquest", "datescene", "messagecard"].includes($("category").value);
  gallery.classList.toggle("character-gallery", characterCategory);
  gallery.classList.toggle("part-gallery", browsingParts);
  ui.attr($("group-search"), "placeholder", browsingParts ? "Filter parts or chapters…" : mainStory ? "Filter chapters in this part…" : characterCategory ? "Character name / Book No." : "Filter events, chapters or characters…");
  for (const group of filtered.slice(0, groupLimit)) {
    const direct = group.directStoryId != null;
    const button = make("button", undefined, `group-tile${browsingParts ? " main-part-tile" : characterCategory ? " character-tile" : ""}`);
    ui.attr(button, "aria-label", `${group.label}${group.qualifier ? ` · ${group.qualifier}` : ""} · ${direct ? group.categoryKey === "datescene" ? "好感度100%を読む" : group.mediaKind || "Open" : group.categoryKey === "messagecard" ? formatCount(group.count, "card") : group.categoryKey === "theater" ? formatCount(group.count, "item") : formatCount(group.count, "story", "stories")}`);
    if (browsingParts) button.dataset.part = group.key;
    else button.dataset.group = group.key;
    if (direct) button.dataset.storyId = group.directStoryId;
    if (browsingParts) {
      const image = bannerImage(group.banner, "", "group-tile-image main-part-image");
      if (image) button.append(image);
      else button.append(makeUI("span", "MAIN STORY", "part-eyebrow"));
    }
    else if (characterCategory) button.append(characterIcon(group.characterIcon, group.characterName || group.label));
    else {
      const img = bannerImage(group.banner, "", "group-tile-image");
      if (img) button.append(img);
    }
    button.append(make("h4", group.label));
    if (group.qualifier) button.append(make("span", group.qualifier, "group-qualifier"));
    if (!direct) button.append(makeUI("small", browsingParts
      ? `${formatCount(group.chapterCount, "chapter")} · ${formatCount(group.count, "story", "stories")}`
      : group.categoryKey === "messagecard" ? formatCount(group.count, "card") : group.categoryKey === "theater" ? formatCount(group.count, "item") : formatCount(group.count, "story", "stories")));
    if (direct && group.categoryKey === "theater") button.append(makeUI("small", group.mediaKind || "Open"));
    if (direct) {
      const indicators = make("span", undefined, "character-tile-indicators");
      if (favorites.has(group.directStoryId)) indicators.append(make("span", "★"));
      if (readStories.has(String(group.directStoryId))) indicators.append(makeUI("span", "Read"));
      button.append(indicators);
      button.classList.toggle("selected", current?.story.id === group.directStoryId);
    }
    button.addEventListener("click", () => {
      rememberCatalog(); clearTimeout(searchTimer);
      if (browsingParts) {
        selectedPart = group.key;
        $("group-search").value = catalogPosition(catalogGroupFilterKey()).query || "";
        renderGroups();
        return loadCards();
      }
      if (direct) return openStory(group.directStoryId);
      $("group").value = group.key;
      return loadCards();
    });
    gallery.append(button);
  }
  if (!filtered.length) gallery.append(makeUI("p", "No matching events, chapters or characters.", "empty-list"));
  if (filtered.length > groupLimit) {
    const more = makeUI("button", `Load more · ${groupLimit}/${filtered.length}`, "more-button group-more");
    more.addEventListener("click", () => { rememberCatalog(); groupLimit += 60; renderGroups(); saveCatalogSelection(); });
    gallery.append(more);
  }
  updateCatalogPanels();
  restoreCatalogPanel("group-panel", groupViewKey);
}

function updateCatalogPanels() {
  const hasGroups = $("category").value === "mainstory" && mainParts.length ? true : groups.length > 1;
  const browsingGroups = !!$("category").value && hasGroups && !$("group").value && !$("search").value.trim() && !favoriteOnly;
  $("group-panel").hidden = !browsingGroups;
  $("story-list").hidden = browsingGroups;
  renderCategoryPreview();
}

function updateLibraryTabs() {
  $("all-tab").classList.toggle("active", !favoriteOnly);
  $("favorite-tab").classList.toggle("active", favoriteOnly);
}

function openLibrary() {
  setAuto(false); pauseTheater();
  renderGroups();
  if (!$("library-dialog").open) $("library-dialog").showModal();
  restoreCatalog();
}

async function loadCards(reset = true) {
  if (loading && !reset) return;
  rememberCatalogPanel("story-list", cardViewKey, { count: cardViewCount });
  const key = catalogCardKey();
  if (reset) { listGeneration++; cards = []; }
  const generation = listGeneration;
  loading = true;
  try {
    const wanted = reset ? Math.max(80, Math.min(20000, Number(catalogPosition(key).count) || 80)) : 80;
    const limit = Math.min(200, wanted);
    const params = new URLSearchParams({ q: $("search").value.trim(), category: $("category").value, group: $("group").value, part: $("category").value === "mainstory" ? selectedPart : "", offset: String(cards.length), limit: String(limit) });
    if (favoriteOnly) params.set("ids", [...favorites].join(","));
    const data = await api(`/api/stories?${params}`);
    if (generation !== listGeneration || key !== catalogCardKey()) return;
    // Restore every previously loaded page, respecting the local API's 200-row
    // page cap. Otherwise a deep scroll position would be clamped at row 200.
    while (reset && data.items.length < Math.min(wanted, data.total)) {
      params.set("offset", String(data.items.length));
      params.set("limit", String(Math.min(200, wanted - data.items.length)));
      const next = await api(`/api/stories?${params}`);
      if (generation !== listGeneration || key !== catalogCardKey()) return;
      if (!next.items.length) break;
      data.items.push(...next.items);
    }
    total = data.total;
    cards.push(...data.items);
    ui.text($("story-total"), $("category").value === "messagecard" ? formatCount(total, "card") : $("category").value === "theater" ? formatCount(total, "item") : formatCount(total, "story", "stories"));
    cardViewKey = key; cardViewCount = cards.length;
    renderCards();
    updateCatalogPanels();
    catalogViews.set(key, { ...catalogPosition(key), count: cardViewCount });
    saveCatalogSelection();
    restoreCatalog();
  } catch (error) { notify(error.message); }
  finally { if (generation === listGeneration) loading = false; }
}

function renderCards() {
  const list = $("story-list");
  list.replaceChildren();
  if (!cards.length) { list.append(makeUI("p", "No matching stories.", "empty-list")); return; }
  let lastGroup = null;
  let lastCategory = null;
  for (const card of cards) {
    if (card.categoryKey !== lastCategory) {
      if (!$("category").value) {
        const img = bannerImage(card.categoryBanner, card.category, "catalog-category-banner");
        if (img) list.append(img);
      }
      lastCategory = card.categoryKey;
    }
    const group = card.groupKey || `${card.categoryKey}:${card.group}`;
    if (group !== lastGroup) {
      const heading = make("h3", undefined, "catalog-group");
      heading.lang = "ja";
      const img = bannerImage(card.banner, card.groupLabel || card.group || card.category, "catalog-group-banner");
      if (img) heading.append(img);
      heading.append(make("small", card.category), make("span", card.groupLabel || card.group || card.category));
      if (card.groupQualifier) heading.append(make("span", card.groupQualifier, "group-qualifier"));
      if (card.bannerText) heading.append(make("small", card.bannerText));
      list.append(heading);
      lastGroup = group;
    }
    const hasCharacter = ["characterquest", "datescene", "messagecard"].includes(card.categoryKey);
    const button = make("button", undefined, `story-card${hasCharacter ? " character-story" : ""}${current?.story.id === card.id ? " selected" : ""}`);
    button.dataset.id = card.id;
    const top = make("div", undefined, "card-top");
    top.append(make("span", card.logicalName));
    const indicators = make("span", undefined, "card-indicators");
    if (favorites.has(card.id)) indicators.append(make("span", "★", "card-progress"));
    if (readStories.has(String(card.id))) indicators.append(makeUI("span", "Read", "card-progress"));
    top.append(indicators);
    // Category and chapter/character already appear in the group heading.
    // Avoid repeating long captions on every entry in the narrow sidebar.
    const caption = ui.t(card.mediaKind) || (card.spotLocation ? `出現場所：${card.spotLocation}` : card.phase || card.category);
    const copy = make("div", undefined, "card-copy");
    copy.append(make("h3", card.displayTitle || card.title), make("p", caption, "card-caption"));
    if (card.resourceKind !== "theater-media") copy.append(make("p", card.characters.slice(0, 3).join(" · ") || card.preview || ui.t("Narration")));
    if (card.categoryKey === "spotstory") {
      const participants = card.participants || [];
      const footer = make("div", undefined, "spot-card-footer");
      const details = make("div", undefined, "spot-card-details");
      details.append(make("p", caption, "card-caption"), make("p", participants.length ? participants.map(person => person.name).join(" · ") : card.characters.slice(0, 3).join(" · ") || card.preview || ui.t("Narration")));
      footer.append(details);
      if (participants.length) {
        const portraits = make("div", undefined, "spot-participants");
        portraits.setAttribute("role", "group");
        portraits.setAttribute("aria-label", "登場キャラクター");
        for (const person of participants) {
          const icon = characterIcon(person.icon, person.name);
          icon.title = person.name;
          icon.setAttribute("role", "img");
          icon.setAttribute("aria-label", person.name);
          portraits.append(icon);
        }
        footer.append(portraits);
      }
      button.classList.toggle("spot-story", true);
      const content = make("div", undefined, "spot-card-content");
      content.append(top, make("h3", card.displayTitle || card.title), footer);
      button.append(content);
    } else if (hasCharacter) {
      const body = make("div", undefined, "character-card-main");
      body.append(characterIcon(card.characterIcon, card.group), copy);
      button.append(top, body);
    } else button.append(top, copy);
    button.addEventListener("click", () => openStory(card.id));
    list.append(button);
  }
  if (cards.length < total) {
    const more = makeUI("button", `Load 80 more · ${cards.length}/${total}`, "more-button");
    more.addEventListener("click", () => loadCards(false));
    list.append(more);
  }
}

function stopAudio() {
  stopLogVoice();
  for (const sound of [bgm, voice, effect]) { if (sound) { sound.pause(); sound.src = ""; } }
  bgm = voice = effect = null;
  lastBgm = lastVoice = ""; lastSe = -1;
}
function play(url, kind, onError) {
  if (!url || muted) return null;
  const sound = new Audio(url);
  sound.loop = kind === "bgm";
  sound.volume = audioPreferences[kind] / 100;
  const failed = error => {
    if (onError) Promise.resolve().then(() => onError(error));
    else if (error?.name === "NotAllowedError") notify("Playback blocked. Turn sound off and on to start audio.");
  };
  try { Promise.resolve(sound.play()).catch(failed); }
  catch (error) { failed(error); }
  return sound;
}

function syncLogVoiceButtons() {
  for (const [key, button] of logVoiceButtons) {
    const active = !!logVoice && logVoiceKey === key;
    ui.text(button, active ? "■ Stop" : "▶ Voice");
    button.classList.toggle("playing", active);
    button.setAttribute("aria-pressed", String(active));
    if (!button.disabled) {
      ui.attr(button, "title", active ? "Stop voice playback" : muted ? "Turn sound on to replay this voice" : "Play this line's voice");
      ui.attr(button, "aria-label", active ? "Stop voice playback" : "Play this line's voice");
    }
  }
}

function stopLogVoice() {
  const sound = logVoice;
  logVoice = null; logVoiceKey = "";
  if (sound) { sound.pause(); sound.src = ""; }
  syncLogVoiceButtons();
}

function logCommandFor(entry) {
  if (entry.kind !== "message" || !current) return null;
  const commands = current.script.commands;
  if (!logVoiceIndexes.has(commands)) {
    logVoiceIndexes.set(commands, new Map(commands.flatMap((command, index) => command.op === "mess"
      ? [[`message:${command.line ?? index + 1}`, command]] : [])));
  }
  const command = logVoiceIndexes.get(commands).get(entry.key);
  // Old bookmarks need no migration: recover voice from the exact original
  // line, never from translated text, an unvisited branch, or a stale log URL.
  if (!command || (command.name || "") !== entry.name || (command.text || "") !== entry.text) return null;
  return command;
}

function logVoiceFor(entry) {
  const command = logCommandFor(entry);
  if (!command?.voice) return null;
  return { reference: command.voice, url: current.assets.resolved.voice?.[command.voice] || null };
}

function logSpeakerIconFor(entry) {
  if (!entry.name?.trim() || !logCommandFor(entry)) return null;
  const icon = current.speakerIcons?.[entry.name];
  return typeof icon?.url === "string" && /^\/api\/asset\/[a-f0-9]{24}$/.test(icon.url) ? icon : null;
}

let logPortraitCache;
function visitedLogPortraits() {
  const key = `${engine.pc}:${engine.log.length}:${JSON.stringify(engine.state.selections)}`;
  if (logPortraitCache?.story === current && logPortraitCache.key === key) return logPortraitCache.actors;
  const actors = new Map(), lastSeen = new Map(), replay = new StoryEngine(current.script.commands, () => {});
  let budget = current.script.commands.length + 1;
  // Replay only the selected, already-visited branch, with no real rendering,
  // audio or storage callbacks. Old bookmarks/log entries need no migration.
  while (replay.pc < engine.pc && !replay.state.ended && budget-- > 0) {
    if (replay.state.choice) {
      const index = StoryEngine.selectedChoiceIndex(engine.state, replay.state.choice, engine.log);
      if (index < 0) break;
      replay.choose(index);
    } else replay.next();
    const visible = new Map();
    for (const actor of Object.values(replay.state.actors)) {
      const name = actor.name.trim(), matches = visible.get(name) || [];
      matches.push(actor); visible.set(name, matches);
    }
    for (const [name, matches] of visible) {
      if (matches.length === 1) lastSeen.set(name, { ...matches[0] });
      else lastSeen.delete(name);
    }
    const message = replay.state.message, matches = message && visible.get(message.name.trim());
    const actor = message && (replay.state.actors[message.name]
      || (matches?.length === 1 ? matches[0] : matches ? null : lastSeen.get(message.name.trim())));
    if (actor && replay.pc <= engine.pc) actors.set(message.line, { ...actor });
  }
  logPortraitCache = { story: current, key, actors }; return actors;
}

const localIconUrl = icon => typeof icon?.url === "string" && /^\/api\/asset\/[a-f0-9]{24}$/.test(icon.url);
const avatarBoundsCache = new WeakMap();
function avatarCrop(asset, image) {
  if (asset.kind === "atlas") {
    const visible = portraitViewport(asset), head = portraitHeadReference(asset);
    const reliable = head && [head.x, head.y, head.w, head.h].every(Number.isFinite) && head.w > 0 && head.h > 0
      && head.w / asset.size.h >= .055 && head.w / asset.size.h <= .45 && head.h / head.w >= .25 && head.h / head.w <= 2.5;
    const side = reliable ? Math.max(head.w * 1.9, head.h * 1.7) : Math.min(visible.w, visible.h * .48);
    return { x: (reliable ? head.x + head.w / 2 : visible.x + visible.w / 2) - side / 2,
      y: reliable ? head.y + head.h / 2 - side / 2 : visible.y, w: side, h: side };
  }
  if (!avatarBoundsCache.has(image)) {
    // Legacy PNGs also have wide transparent margins. Inspect a small local
    // alpha mask, not the full-resolution image or an inferred character ID.
    const probe = make("canvas"), scale = Math.min(1, 384 / Math.max(image.width, image.height));
    probe.width = Math.max(1, Math.round(image.width * scale)); probe.height = Math.max(1, Math.round(image.height * scale));
    const context = probe.getContext("2d", { willReadFrequently: true });
    context.drawImage(image, 0, 0, probe.width, probe.height);
    const pixels = context.getImageData(0, 0, probe.width, probe.height).data;
    let left = probe.width, right = -1, top = probe.height, bottom = -1;
    for (let y = 0; y < probe.height; y++) for (let x = 0; x < probe.width; x++) {
      if (pixels[(y * probe.width + x) * 4 + 3] < 32) continue;
      left = Math.min(left, x); right = Math.max(right, x); top = Math.min(top, y); bottom = Math.max(bottom, y);
    }
    const bounds = right >= left ? { x: left / scale, y: top / scale, w: (right - left + 1) / scale, h: (bottom - top + 1) / scale }
      : { x: 0, y: 0, w: image.width, h: image.height };
    avatarBoundsCache.set(image, bounds);
  }
  const bounds = avatarBoundsCache.get(image), side = Math.min(bounds.w, bounds.h * .55);
  return { x: bounds.x + (bounds.w - side) / 2, y: bounds.y, w: side, h: side };
}

function characterAvatar(icon, portrait, className, onFailure = () => {}) {
  if (!localIconUrl(icon) && !localIconUrl(portrait)) return null;
  const box = make("span", undefined, className); box.setAttribute("aria-hidden", "true");
  const fail = () => { box.hidden = true; onFailure(); };
  const drawPortrait = async () => {
    if (!localIconUrl(portrait)) return fail();
    try {
      const image = await cachedImage(portrait.url), crop = avatarCrop(portrait, image);
      const canvas = make("canvas"); canvas.width = canvas.height = 120;
      const context = canvas.getContext("2d");
      context.scale(120 / crop.w, 120 / crop.h); context.translate(-crop.x, -crop.y);
      if (portrait.kind === "atlas") {
        for (const item of [portrait.main, portrait.expression].filter(Boolean)) {
          const f = item.frame, p = item.spriteSourceSize;
          context.drawImage(image, f.x, f.y, f.w, f.h, p.x, p.y, f.w, f.h);
        }
      } else context.drawImage(image, 0, 0);
      box.replaceChildren(canvas); box.dataset.source = "portrait";
    } catch { fail(); }
  };
  if (localIconUrl(icon)) {
    const image = make("img"); image.src = icon.url; image.alt = ""; image.loading = "lazy"; image.decoding = "async";
    image.width = image.height = 100; image.addEventListener("error", drawPortrait); box.append(image);
  } else drawPortrait();
  return box;
}

function appendLogSpeakerIcon(row, entry) {
  if (!entry.name?.trim() || /[?？■▓]/.test(entry.name) || !logCommandFor(entry)) return;
  const actor = visitedLogPortraits().get(entry.line), portrait = actor && current.assets.resolved.character?.[actor.file];
  const icon = actor && current.portraitIcons?.[actor.file] || logSpeakerIconFor(entry);
  const box = characterAvatar(icon, portrait, "log-speaker-avatar", () => row.classList.toggle("has-avatar", false));
  if (!box) return;
  row.append(box);
  row.classList.toggle("has-avatar", true);
}

function playLogVoice(entry, storyId = current?.story.id) {
  if (!engine || current?.story.id !== storyId || !engine.log.some(row => row.key === entry.key && row.text === entry.text)) return;
  const audio = logVoiceFor(entry);
  if (!audio?.url) return;
  if (logVoice && logVoiceKey === entry.key) return stopLogVoice();
  if (muted) { notify("Sound is off. Use Sound off in History to enable playback."); return; }
  stopLogVoice();
  // Only one voice at a time. BGM and sound effects keep their own channels.
  if (voice) { voice.pause(); voice.src = ""; voice = null; }
  let sound;
  const finish = error => {
    if (logVoice !== sound) return; // Ignore late events from an older player.
    stopLogVoice();
    if (error) notify(error.name === "NotAllowedError" ? "Playback blocked. Click Voice to try again." : "Could not play this voice. Check the local audio file.");
  };
  sound = play(audio.url, "voice", error => finish(error));
  logVoice = sound; logVoiceKey = entry.key;
  if (sound) {
    sound.onended = () => finish();
    sound.onerror = () => finish(new Error("Audio unavailable"));
  }
  syncLogVoiceButtons();
}

function toggleSound(fromHistory = false) {
  muted = !muted; store.put("muted", muted); setMode();
  if (muted) stopAudio();
  else if (engine && current && fromHistory) {
    // Enabling sound in History must not replay the current dialogue/effect,
    // rewrite a bookmark, or call renderState/engine.next().
    lastBgm = engine.state.bgm;
    bgm = play(current.assets.resolved.bgm?.[lastBgm], "bgm");
    lastVoice = `${engine.state.message?.line}:${engine.state.voice}`;
    lastSe = engine.state.seSerial;
  } else if (engine) { lastBgm = lastVoice = ""; lastSe = -1; renderState(engine.state); }
  syncLogVoiceButtons();
}

function updateHeading() {
  const card = current.story;
  $("story-category").textContent = card.category;
  $("story-title").textContent = card.displayTitle || card.title;
  const groupCaption = card.groupLabel || card.group;
  $("story-subtitle").textContent = [groupCaption, card.phase, card.spotLocation ? `出現場所：${card.spotLocation}` : ""].filter(Boolean).join(" · ");
  const banner = card.banner || card.categoryBanner;
  const image = $("story-banner");
  image.hidden = !banner?.url;
  image.onerror = () => { image.hidden = true; };
  if (banner?.url) { image.src = banner.url; image.alt = card.banner ? (card.groupLabel || card.group || card.title) : card.category; }
  else image.removeAttribute("src");
  updateReadButton();
}

function updateReadButton() {
  const marked = !!current && readStories.has(String(current.story.id));
  const button = $("read-button");
  button.disabled = !current;
  ui.text(button, marked ? "Mark as unread" : "Mark as read");
  button.setAttribute("aria-pressed", String(marked));
  ui.attr(button, "title", marked ? "Mark as unread and keep your reading position" : "Mark this story as read manually");
}

async function refreshCurrentMetadata() {
  if (!current) return;
  const generation = loadGeneration;
  const payload = await api(`/api/story/${current.story.id}`);
  if (generation !== loadGeneration) return;
  if (!!payload.theaterMedia !== !!current.theaterMedia) return openStory(payload.story.id, false, false);
  current = payload;
  updateHeading();
  if (current.theaterMedia) renderTheater();
  else if (current.story.resourceKind === "messagecard") renderMessageCard();
  else if (engine) renderState(engine.state);
}

async function openStory(id, restart = false, closeCatalog = true) {
  const generation = ++loadGeneration;
  setAuto(false); stopAudio(); pauseTheater();
  if ($("log-dialog").open) $("log-dialog").close();
  try {
    const payload = await api(`/api/story/${id}`);
    if (generation !== loadGeneration) return;
    stopTheater();
    current = payload;
    dialogueHidden = false;
    updateHeading();
    $("welcome").hidden = true;
    $("dialogue").hidden = false;
    for (const id of ["favorite-button", "previous", "restart", "auto", "diagnostics", "log-button"]) $(id).disabled = false;
    const messagecard = payload.story.resourceKind === "messagecard";
    const media = !!payload.theaterMedia;
    for (const id of ["previous", "auto", "audio-button"]) $(id).hidden = messagecard || media;
    $("restart").hidden = messagecard || (media && !payload.theaterMedia.url);
    for (const id of ["mode-button", "reading-settings-button", "diagnostics", "log-button"]) $(id).hidden = media;
    $("hide-text-button").hidden = media;
    $("auto").disabled = messagecard || media;
    $("favorite-button").textContent = favorites.has(payload.story.id) ? "★" : "☆";
    engine = media ? null : new StoryEngine(payload.script.commands, renderState);
    $("theater-view").hidden = !media;
    setMode();
    const saved = store.get(`progress:${id}`, null);
    if (media) renderTheater(restart);
    else if (!restart && saved?.revision === payload.revision && engine.restore(saved.snapshot, saved.log)) notify("Reading position restored");
    else engine.next();
    store.put("lastStory", id);
    renderCards();
    if (closeCatalog && $("library-dialog").open) { rememberCatalog(); $("library-dialog").close(); }
    ui.text($("library-button"), "Library ▦");
    if (payload.assets.missing.length) ui.text($("diagnostics"), `Assets · ${payload.assets.missing.length} missing`);
    else ui.text($("diagnostics"), "Assets");
  } catch (error) { notify(error.message); }
}

function saveTheaterPosition() {
  if (!current?.theaterMedia?.url) return;
  const video = $("theater-video");
  if (video.readyState < 1 || video.getAttribute?.("src") !== current.theaterMedia.url) return;
  const time = Number(video.currentTime);
  if (Number.isFinite(time) && time >= 0) store.put(`progress:${current.story.id}`, { revision: current.revision, theaterTime: time });
}
function pauseTheater() {
  if (!current?.theaterMedia) return;
  saveTheaterPosition(); $("theater-video").pause?.();
}
function stopTheater() {
  pauseTheater();
  const video = $("theater-video");
  video.removeAttribute("src"); video.dataset.resumeTime = "0"; video.load?.();
}
function renderTheater(restart = false) {
  const media = current.theaterMedia, video = $("theater-video");
  $("dialogue").hidden = $("choices").hidden = $("messagecard-view").hidden = true;
  $("theater-view").hidden = false;
  video.hidden = !media.url;
  $("theater-note").hidden = !media.url;
  ui.text($("theater-status"), media.notice || (media.url ? "" : "Video is not downloaded yet. Rescan the library after downloading theater assets."));
  $("theater-status").hidden = !!media.url;
  if (media.url && video.getAttribute?.("src") !== media.url) {
    const saved = store.get(`progress:${current.story.id}`, null);
    video.dataset.resumeTime = String(!restart && saved?.revision === current.revision && Number.isFinite(saved.theaterTime) ? Math.max(0, saved.theaterTime) : 0);
    video.src = media.url; video.load?.();
  }
}
$("theater-video").addEventListener("loadedmetadata", () => {
  if (!current?.theaterMedia?.url) return;
  const video = $("theater-video"), duration = Number(video.duration), time = Number(video.dataset.resumeTime || 0);
  if (Number.isFinite(duration) && duration > 0) video.currentTime = Math.min(time, duration);
});
for (const type of ["timeupdate", "pause", "seeked", "ended"]) $("theater-video").addEventListener(type, saveTheaterPosition);
$("theater-video").addEventListener("error", () => {
  if (!current?.theaterMedia?.url || !$("theater-video").getAttribute?.("src")) return;
  $("theater-status").hidden = false;
  ui.text($("theater-status"), "Could not play this local video. Its format may not be supported by this browser.");
});

function cachedImage(url) {
  if (!imageCache.has(url)) imageCache.set(url, new Promise((resolve, reject) => {
    const image = new Image(); image.onload = () => resolve(image); image.onerror = () => reject(new Error("Could not load image")); image.src = url;
  }));
  return imageCache.get(url);
}

function portraitViewport(asset, characters = {}) {
  const width = asset.size.w, height = asset.size.h;
  // Keep a fixed window for this body throughout the story, including all
  // referenced expressions. Original virtual canvases have huge empty margins.
  const frames = [asset.main, asset.expression];
  for (const sibling of Object.values(characters)) {
    if (sibling.kind === "atlas" && sibling.url === asset.url && sibling.expression &&
        sibling.main.frame.x === asset.main.frame.x && sibling.main.frame.y === asset.main.frame.y) {
      frames.push(sibling.expression);
    }
  }
  let left = width, top = height, right = 0, bottom = 0;
  for (const frame of frames.filter(Boolean)) {
    const { x, y } = frame.spriteSourceSize;
    left = Math.min(left, x); top = Math.min(top, y);
    right = Math.max(right, x + frame.frame.w); bottom = Math.max(bottom, y + frame.frame.h);
  }
  // Preserve the original canvas clipping (some bodies extend past its edge).
  left = Math.max(0, Math.floor(left - 8)); top = Math.max(0, Math.floor(top - 8));
  right = Math.min(width, Math.ceil(right + 8)); bottom = Math.min(height, Math.ceil(bottom + 8));
  return right > left && bottom > top ? { x: left, y: top, w: right - left, h: bottom - top } :
    { x: 0, y: 0, w: width, h: height };
}

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
// ConstEnvironment.SCREEN_H in the original game is 640, independent of the
// 640/840px virtual canvases used by different portrait atlas generations.
const GAME_STAGE_HEIGHT = 640;
const median = values => { const sorted = [...values].sort((a, b) => a - b); const middle = Math.floor(sorted.length / 2); return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2; };

function portraitPoseKey(actor, asset) {
  let name = actor.file.replace(/^character\/(?:s|cutin)\//, "").replace(/\.png$/, "");
  if (asset.kind === "atlas" && /^bustup_[0-9]{6,9}(?:_[A-Za-z0-9]+)*$/.test(name)) name = name.replace(/_[0-9]{2}$/, "");
  return `${asset.kind}:${name}`;
}

function portraitAdjustment(key) {
  const saved = portraitAdjustments[key];
  return { scale: clamp(Number(saved?.scale) || 100, 70, 140), y: clamp(Number(saved?.y) || 0, -20, 20) };
}

function portraitHeadReference(asset, characters = {}) {
  if (asset.headReference) return asset.headReference;
  // Compatibility with an older running backend. The updated backend uses all
  // expressions in the atlas, including ones this story never displays.
  const patches = Object.values(characters).filter(item => item.kind === "atlas" && item.url === asset.url && item.expression &&
    item.main.frame.x === asset.main.frame.x && item.main.frame.y === asset.main.frame.y).map(item => item.expression);
  if (!patches.length) return null;
  const w = median(patches.map(frame => frame.frame.w)), h = median(patches.map(frame => frame.frame.h));
  return { w, h, x: median(patches.map(frame => frame.spriteSourceSize.x + frame.frame.w / 2)) - w / 2,
    y: median(patches.map(frame => frame.spriteSourceSize.y + frame.frame.h / 2)) - h / 2 };
}

function portraitLayout(asset, viewport, head, adjustment = { scale: 100, y: 0 }) {
  let unit = .96 / viewport.h, top = 1 - .96, anchor = .5, calibrated = false;
  if (head && [head.x, head.y, head.w, head.h].every(Number.isFinite) && head.w / asset.size.h >= .055 &&
      head.w / asset.size.h <= .45 && head.h / head.w >= .25 && head.h / head.w <= 2.5 &&
      head.x + head.w / 2 >= 0 && head.x + head.w / 2 <= asset.size.w &&
      head.y + head.h / 2 >= 0 && head.y + head.h / 2 <= asset.size.h) {
    unit = clamp(.16 / head.w, unit * .7, unit * 1.3);
    const centerY = head.y + head.h / 2;
    // Retain a small part of the original height difference; don't force every
    // face onto a perfectly flat line or scale from ears, hats and weapons.
    const baseline = .32 + clamp((centerY / asset.size.h - .36) * .18, -.045, .045);
    unit *= clamp(Number(adjustment.scale) || 100, 70, 140) / 100;
    top = baseline - (centerY - viewport.y) * unit;
    anchor = (head.x + head.w / 2 - viewport.x) / viewport.w;
    calibrated = true;
  } else {
    unit *= clamp(Number(adjustment.scale) || 100, 70, 140) / 100;
    top = 1 - viewport.h * unit;
  }
  const height = viewport.h * unit;
  // Face alignment can leave a short bust's cut edge above the stage bottom.
  // Lower it without enlarging the face or lifting portraits already extending
  // below the stage. Apply explicit manual/story offsets after this constraint.
  top = Math.max(top, 1 - height);
  return { height, top: top + clamp(Number(adjustment.y) || 0, -20, 20) / 100, anchor, calibrated };
}

function applyPortraitLayout(actor, target, asset, viewport, head) {
  const adjustment = portraitAdjustment(portraitPoseKey(actor, asset));
  const layout = portraitLayout(asset, viewport, head, { scale: adjustment.scale, y: 0 });
  const sceneScale = Number.isFinite(actor.scale) && actor.scale > 0 ? actor.scale : 1;
  const height = layout.height * sceneScale;
  const originY = Number.isFinite(actor.originY) ? actor.originY : Number(actor.y) || 0;
  const motionY = (Number(actor.y) || 0) - originY;
  // Legacy scripts pair close-up scale with a negative entrance offset. Apply
  // both before checking the cut edge; retain later movement and manual tuning.
  const top = Math.max(layout.top + originY / GAME_STAGE_HEIGHT, 1 - height)
    + motionY / GAME_STAGE_HEIGHT + adjustment.y / 100;
  target.style.height = `${height * 100}%`;
  target.style.top = `${top * 100}%`;
  target.style.bottom = "auto";
  target.style.aspectRatio = `${viewport.w} / ${viewport.h}`;
  target.style.transformOrigin = `${layout.anchor * 100}% 50%`;
  target.style.transform = `translateX(${-layout.anchor * 100}%) scaleX(${actor.reverse || 1})`;
  target.dataset.calibrated = String(layout.calibrated);
}

async function drawActor(actor, target, generation, resolved) {
  const asset = resolved.character?.[actor.file];
  if (!asset) {
    const placeholder = make("div", undefined, "missing-actor");
    placeholder.append(make("span", actor.name || ui.t("Character")), makeUI("small", "Portrait not cached"));
    target.append(placeholder); return;
  }
  try {
    const image = await cachedImage(asset.url);
    if (generation !== renderGeneration) return;
    if (asset.kind === "image") {
      const viewport = { x: 0, y: 0, w: image.width, h: image.height };
      applyPortraitLayout(actor, target, { ...asset, size: { w: image.width, h: image.height } }, viewport, null);
      const img = make("img"); img.src = asset.url; img.alt = actor.name; target.append(img); return;
    }
    const viewport = portraitViewport(asset, resolved.character);
    applyPortraitLayout(actor, target, asset, viewport, portraitHeadReference(asset, resolved.character));
    const canvas = make("canvas"); canvas.setAttribute("aria-label", actor.name);
    canvas.width = viewport.w; canvas.height = viewport.h;
    const context = canvas.getContext("2d");
    // Clip in virtual-canvas coordinates before translating the cropped window.
    context.translate(-viewport.x, -viewport.y);
    context.beginPath(); context.rect(0, 0, asset.size.w, asset.size.h); context.clip();
    const main = asset.main.frame, destination = asset.main.spriteSourceSize;
    context.drawImage(image, main.x, main.y, main.w, main.h, destination.x, destination.y, main.w, main.h);
    if (asset.expression) {
      const frame = asset.expression.frame, face = asset.expression.spriteSourceSize;
      context.drawImage(image, frame.x, frame.y, frame.w, frame.h, face.x, face.y, frame.w, frame.h);
    }
    target.append(canvas);
  } catch { target.append(makeUI("div", "Could not load portrait", "missing-actor")); }
}

function renderActors(state, resolved, generation) {
  $("actors").replaceChildren();
  $("portrait-settings").disabled = true;
  for (const actor of Object.values(state.actors)) {
    const node = make("div", undefined, "actor");
    node.style.left = `${({ 0: 50, 1: 22, 2: 78 }[actor.position] ?? 50) + actor.x / 11.36}%`;
    node.style.bottom = `${-actor.y / GAME_STAGE_HEIGHT * 100}%`;
    node.style.transform = `translateX(-50%) scaleX(${actor.reverse || 1})`;
    $("actors").append(node);
    if (resolved.character?.[actor.file]) $("portrait-settings").disabled = false;
    drawActor(actor, node, generation, resolved);
  }
}

function renderDialogueText(state) {
  const languages = readingLanguages(), bilingual = languages.length === 2;
  const japanese = languages.includes("ja"), chinese = languages.includes("zh");
  $("speaker").textContent = state.message?.name || "";
  $("message").textContent = state.message?.text || (state.choice ? ui.t("Choose a response.") : "");
  $("message").scrollTop = 0;
  $("stage-shell").classList.toggle("bilingual", bilingual);
  $("dialogue").classList.toggle("bilingual", bilingual);
  $("dialogue").classList.toggle("zh-first", bilingual && languages[0] === "zh");
  $("dialogue").classList.toggle("chinese-only", !japanese && chinese);
  $("speaker").hidden = $("message").hidden = !japanese;
  $("speaker-translation").hidden = $("message-translation").hidden = !chinese;
  $("speaker-translation").textContent = chinese && state.message?.name?.trim()
    ? translatedField(`mess:${state.message.line}:name`, state.message.name) ?? ui.t("Untranslated") : "";
  $("message-translation").textContent = chinese && state.message
    ? translatedField(`mess:${state.message.line}:text`, state.message.text) ?? ui.t("No Chinese translation available") : "";
  $("message-translation").scrollTop = 0;
  $("message-number").textContent = `${state.messageNumber} / ${current.story.messageCount}`;
  $("edit-translation").hidden = readingPreferences.language === "ja" || !state.message || !canRevise(`mess:${state.message.line}:text`, state.message.text);
  ui.text($("edit-translation"), state.message && personalTranslation(`mess:${state.message.line}:text`, state.message.text)
    ? "Edit revision" : "Edit translation");
  $("choices").replaceChildren();
  $("choices").hidden = !state.choice;
  if (state.choice) {
    setAuto(false);
    for (const [index, item] of state.choice.items.entries()) {
      const button = make("button", (item.text || ui.t("Continue")).replaceAll("\\n", "\n"), "choice-button");
      const translated = translatedField(StoryEngine.choiceFieldId(state.choice, index), item.text);
      const columns = textColumns(button.textContent, translated === null ? null : translated.replaceAll("\\n", "\n"));
      button.replaceChildren(...columns.map(column => {
        const span = make("span", column.text); span.lang = column.language === "ja" ? "ja" : "zh-Hans"; return span;
      }));
      button.classList.toggle("bilingual-choice", bilingual);
      button.addEventListener("click", () => engine.choose(index));
      $("choices").append(button);
    }
  }
  syncReadingSettings();
}

function renderState(state) {
  if (!current) return;
  const generation = ++renderGeneration;
  syncDialogueVisibility();
  if (current.story.resourceKind === "messagecard") {
    $("stage-shell").classList.toggle("bilingual", false);
    $("dialogue").hidden = true;
    $("choices").hidden = true;
    $("messagecard-view").hidden = false;
    renderMessageCard();
    store.put(`progress:${current.story.id}`, { revision: current.revision, snapshot: engine.snapshot(), log: engine.log });
    return;
  }
  $("messagecard-view").hidden = true;
  $("dialogue").hidden = false;
  const resolved = current.assets.resolved;
  const background = resolved.background?.[state.background];
  $("background").style.backgroundImage = background ? `url("${background}")` : "none";
  $("scene-label").hidden = !state.background || !!background;
  ui.text($("scene-label"), "Background not cached");
  renderActors(state, resolved, generation);
  renderDialogueText(state);
  $("advance").disabled = !!state.choice || state.ended;
  ui.text($("advance"), state.ended ? "End ✓" : "Next ▸");
  ui.text($("reading-hint"), state.ended ? "End of story" : state.choice ? "Choose a response" : "");
  $("previous").disabled = engine.history.length < 2 && !state.ended;
  store.put(`progress:${current.story.id}`, { revision: current.revision, snapshot: engine.snapshot(), log: engine.log });
  if (state.bgm !== lastBgm) {
    bgm?.pause(); lastBgm = state.bgm;
    bgm = play(resolved.bgm?.[state.bgm], "bgm");
  }
  const voiceKey = `${state.message?.line}:${state.voice}`;
  if (voiceKey !== lastVoice) {
    stopLogVoice();
    voice?.pause(); lastVoice = voiceKey;
    voice = play(resolved.voice?.[state.voice], "voice");
  }
  if (state.seSerial !== lastSe) {
    effect?.pause(); lastSe = state.seSerial;
    effect = play(resolved.se?.[state.se], "se");
  }
  if (state.ended) setAuto(false);
  else scheduleAuto();
}

function renderMessageCard() {
  const card = current.story, message = current.messageCard;
  $("messagecard-view").className = `messagecard-view season-${message.groupId}`;
  $("messagecard-icon").replaceChildren(characterIcon(card.characterIcon, card.characterName));
  $("messagecard-season").textContent = card.phase;
  $("messagecard-sender").textContent = card.characterName;
  $("messagecard-title").textContent = card.title;
  renderMessageCardText();
  const mount = $("messagecard-image"); mount.replaceChildren();
  if (message.textImagePath) {
    const url = current.assets.resolved.image?.[message.textImagePath];
    if (url) {
      const image = make("img", undefined, "messagecard-text-image");
      image.src = url; image.alt = `${card.characterName} · ${card.title} · Original text image`;
      image.addEventListener("error", () => { image.hidden = true; mount.append(makeUI("p", "Could not load the card's text image. Please check the asset.", "muted")); });
      mount.append(image);
    } else mount.append(makeUI("p", "This card's text is an image that has not been downloaded yet.", "muted"));
  }
}

function renderMessageCardText() {
  const message = current.messageCard, body = $("messagecard-body"), view = $("messagecard-view");
  const languages = readingLanguages(), imageCard = message.messageType === 2;
  const translated = messageCardTranslation(), hasText = !!messageCardSource().trim();
  const command = current.script.commands.find(command => command.op === "mess");
  const identity = command ? `mess:${command.line}:text` : "";
  const editable = readingPreferences.language !== "ja" && canRevise(identity, messageCardSource());
  $("messagecard-actions").hidden = $("messagecard-edit").hidden = !editable;
  ui.text($("messagecard-edit"), personalTranslation(identity, messageCardSource()) ? "Edit revision" : "Edit translation");
  const bilingual = languages.length === 2 && (!imageCard || hasText);
  view.classList.toggle("bilingual-card", bilingual);
  view.classList.toggle("image-card-bilingual", bilingual && imageCard);
  view.classList.toggle("card-zh-first", bilingual && languages[0] === "zh");
  body.classList.toggle("bilingual-card-text", bilingual && !imageCard);
  // The original art is the Japanese column for OCR cards. Decorative cards
  // without letter text always retain their art; no translation is invented.
  $("messagecard-image").hidden = imageCard && hasText && !languages.includes("ja");
  body.hidden = imageCard && (!hasText || !languages.includes("zh"));
  if (imageCard) {
    body.lang = "zh-Hans";
    if (translated === null) ui.text(body, "No Chinese translation available");
    else ui.plain(body, translated);
  } else if (bilingual) {
    ui.plain(body, ""); body.lang = "ja";
    body.replaceChildren(...textColumns(messageCardText(message), translated).map(column => {
      const node = make("div", column.text, "messagecard-column");
      node.lang = column.language === "ja" ? "ja" : "zh-Hans";
      if (column.language === "zh" && translated === null) ui.text(node, "No Chinese translation available");
      return node;
    }));
  } else {
    body.lang = languages[0] === "zh" ? "zh-Hans" : "ja";
    if (languages[0] === "zh" && translated === null) ui.text(body, "No Chinese translation available");
    else ui.plain(body, languages[0] === "zh" ? translated : messageCardText(message));
  }
}

function setAuto(value) {
  if (value && current?.theaterMedia) return;
  if (value && current?.story.resourceKind === "messagecard") return;
  if (value && dialogueHidden) setDialogueHidden(false);
  autoMode = value;
  clearTimeout(autoTimer);
  $("auto").classList.toggle("active", value);
  ui.text($("auto"), value ? "Pause auto" : "Auto");
  if (value) scheduleAuto();
}
function scheduleAuto() {
  clearTimeout(autoTimer);
  if (!autoMode || !engine || engine.state.choice || engine.state.ended) return;
  const delay = Math.max(2200, Math.min(16000, 1200 + (engine.state.message?.text.length || 0) * 100));
  autoTimer = setTimeout(() => { engine.next(); }, delay);
}
function showDiagnostics() {
  if (!current) return;
  const references = current.script.references;
  const count = Object.values(references).reduce((sum, group) => sum + group.length, 0);
  ui.text($("diagnostics-summary"), `${count} assets referenced; ${current.assets.missing.length} missing locally. ${current.script.warnings.length} parsing notes. Text remains readable without missing assets.`);
  const list = $("diagnostics-list"); list.replaceChildren();
  list.append(makeUI("div", `Category source: ${current.story.categorySource || "No master data linked"}; title source: ${current.story.titleSource || "None; showing story ID"}`, "diagnostic-item"));
  const labels = { character: "Portrait", background: "Background", bgm: "BGM", se: "Sound effect", voice: "Voice", image: "Image", movie: "Video" };
  for (const item of current.assets.missing) {
    const row = make("div", undefined, "diagnostic-item"); row.append(makeUI("strong", labels[item.kind] || item.kind), make("span", item.name)); list.append(row);
  }
  for (const warning of current.script.warnings) list.append(makeUI("div", `Line ${warning.line}: ${warning.message}`, "diagnostic-item"));
  const skipped = Object.keys(engine.state.skipped);
  if (skipped.length) list.append(makeUI("div", `Unsupported scene commands: ${skipped.join(", ")}`, "diagnostic-item"));
  if (!list.childElementCount) list.append(makeUI("p", "All local assets for this story were found.", "muted"));
  $("diagnostics-dialog").showModal();
}

function logChoiceField(entry) {
  const line = entry.line ?? Number(/^choice:(\d+):/.exec(entry.key || "")?.[1]);
  const command = current?.script?.commands.find(command => command.op === "choice" && command.line === line);
  if (!command) return null;
  const matches = (command.data?.list || []).flatMap((item, index) => item.text.replaceAll("\\n", "\n") === entry.text
    ? [StoryEngine.choiceFieldId(command, index)] : []);
  const explicit = entry.sourceIdentity || `${entry.key}:text`;
  return matches.includes(explicit) ? explicit : matches.length === 1 ? matches[0] : null;
}

function renderLog() {
  if (!engine || !current) return;
  const query = normalizeSearch($("log-search").value);
  const list = $("log-list"); list.replaceChildren();
  logVoiceButtons.clear();
  const entries = engine.log;
  const isCard = current.story.resourceKind === "messagecard";
  const cardText = isCard && messageCardSource().trim()
    ? messageCardText(current.messageCard) : null;
  const languages = readingLanguages(), bilingual = languages.length === 2;
  $("log-dialog").classList.toggle("bilingual-log", bilingual);
  const localized = entries.map(entry => {
    let translated = null;
    if (entry.kind === "choice") {
      // Log stores display newlines; choice source fields retain escaped ones.
      // Keep both the visited choice identity and exact original text checks.
      const identity = logChoiceField(entry), source = identity ? scriptField(identity) : null;
      if (typeof source === "string" && source.replaceAll("\\n", "\n") === entry.text) {
        translated = translatedField(identity, source)?.replaceAll("\\n", "\n") ?? null;
      }
    } else translated = isCard ? messageCardTranslation() : translatedField(`mess:${entry.line}:text`, entry.text);
    const nameZh = entry.kind === "choice" ? ui.t("Selected response")
      : entry.name?.trim() ? translatedField(`mess:${entry.line}:name`, entry.name) ?? ui.t("Untranslated") : "";
    return { entry, untranslated: translated === null, columns: textColumns(cardText ?? entry.text, translated).map(column => ({ ...column,
      name: column.language === "zh" ? nameZh : entry.kind === "choice" ? ui.t("Selected response") : entry.name || "" })) };
  });
  const visible = localized.filter(item => item.columns.some(column => normalizeSearch(`${column.name} ${column.text}`).includes(query)));
  ui.text($("log-summary"), `${current.story.displayTitle || current.story.title} · ${formatCount(engine.log.filter(entry => entry.kind === "message").length, "line")} viewed`);
  for (const [index, { entry, columns, untranslated }] of visible.entries()) {
    const row = make("article", undefined, `log-entry${entry.kind === "choice" ? " choice-entry" : ""}`);
    const header = make("div", undefined, `log-entry-header${bilingual ? " bilingual-log-header" : ""}`);
    const body = make("div", undefined, `log-text-columns${bilingual ? " bilingual-log-columns" : ""}`);
    for (const column of columns) {
      const name = make("strong", column.name), text = make("p", column.text);
      if (entry.kind === "choice") ui.text(name, "Selected response");
      else if (column.language === "zh" && entry.name?.trim() && translatedField(`mess:${entry.line}:name`, entry.name) === null) ui.text(name, "Untranslated");
      if (column.language === "zh" && untranslated) ui.text(text, "No Chinese translation available");
      name.lang = text.lang = column.language === "ja" ? "ja" : "zh-Hans";
      header.append(name); body.append(text);
    }
    header.append(makeUI("small", entry.kind === "choice" ? "CHOICE" : String(index + 1).padStart(2, "0")));
    row.append(header, body);
    appendLogSpeakerIcon(row, entry);
    const editIdentity = entry.kind === "choice" ? logChoiceField(entry) : `mess:${entry.line}:text`;
    const editSource = entry.kind === "choice" ? scriptField(editIdentity) : isCard ? messageCardSource() : entry.text;
    if (readingPreferences.language !== "ja" && canRevise(editIdentity, editSource)
        && (entry.kind !== "choice" || editSource.replaceAll("\\n", "\n") === entry.text)) {
      const actions = make("div", undefined, "log-entry-edit"), button = makeUI("button",
        personalTranslation(editIdentity, editSource) ? "Edit revision" : "Edit translation", "edit-line-button");
      const storyId = current.story.id;
      button.type = "button";
      button.addEventListener("click", () => { if (current?.story.id === storyId) showTranslationEditor(editIdentity, editSource); });
      actions.append(button); row.append(actions);
    }
    const audio = logVoiceFor(entry);
    if (audio) {
      const actions = make("div", undefined, "log-entry-audio");
      const button = makeUI("button", "▶ Voice", "log-voice-button");
      button.type = "button";
      button.disabled = !audio.url;
      if (!audio.url) {
        ui.attr(button, "title", "Voice not available locally");
        ui.attr(button, "aria-label", "Voice not available locally");
      }
      const storyId = current.story.id;
      button.addEventListener("click", () => playLogVoice(entry, storyId));
      logVoiceButtons.set(entry.key, button);
      actions.append(button); row.append(actions);
    }
    list.append(row);
  }
  if (logVoice && !visible.some(({ entry }) => entry.key === logVoiceKey)) stopLogVoice();
  syncLogVoiceButtons();
  if (!visible.length) list.append(makeUI("p", query ? "No matching dialogue." : "No dialogue recorded yet.", "empty-list"));
}

function showLog() {
  if (!engine || !current) return;
  setAuto(false);
  $("log-search").value = "";
  renderLog();
  if (!$("log-dialog").open) $("log-dialog").showModal();
  $("log-list").scrollTop = $("log-list").scrollHeight;
}

function selectedPortrait() {
  if (!current || !engine) return null;
  for (const actor of Object.values(engine.state.actors)) {
    const asset = current.assets.resolved.character?.[actor.file];
    if (asset && portraitPoseKey(actor, asset) === $("portrait-character").value) return { actor, asset };
  }
  return null;
}

function updatePortraitControls() {
  const selected = selectedPortrait();
  if (!selected) return;
  for (const button of $("portrait-character-list").children) button.setAttribute("aria-pressed", String(button.dataset.pose === $("portrait-character").value));
  const adjustment = portraitAdjustment(portraitPoseKey(selected.actor, selected.asset));
  $("portrait-scale").value = adjustment.scale;
  $("portrait-y").value = adjustment.y;
  $("portrait-scale-number").value = String(adjustment.scale);
  $("portrait-y-number").value = String(adjustment.y);
  $("portrait-scale-value").textContent = $("portrait-y-value").textContent = "%";
  const { asset } = selected;
  const calibrated = asset.kind === "atlas" && portraitLayout(asset, portraitViewport(asset, current.assets.resolved.character),
    portraitHeadReference(asset, current.assets.resolved.character)).calibrated;
  ui.text($("portrait-reference"), calibrated ? "" : "No reliable face reference for this image. Use the sliders to fine-tune its placement.");
}

function showPortraitCalibration() {
  if (!current || !engine) return;
  setAuto(false);
  const choices = new Map();
  for (const actor of Object.values(engine.state.actors)) {
    const asset = current.assets.resolved.character?.[actor.file];
    if (asset) choices.set(portraitPoseKey(actor, asset), { actor, asset });
  }
  if (!choices.size) return notify("No portraits available to align in this scene.");
  const picker = $("portrait-character"), previous = picker.value;
  picker.replaceChildren(); $("portrait-character-list").replaceChildren();
  for (const [key, { actor, asset }] of choices) {
    const name = actor.name || ui.t("Character"), option = make("option", name); option.value = key; picker.append(option);
    const button = make("button", undefined, "portrait-character-option"); button.type = "button"; button.dataset.pose = key;
    const icon = current.portraitIcons?.[actor.file] || current.speakerIcons?.[actor.name];
    const avatar = characterAvatar(icon, asset, "portrait-character-avatar");
    if (avatar) button.append(avatar);
    const label = make("span", name); label.lang = "ja"; button.append(label);
    button.addEventListener("click", () => { picker.value = key; updatePortraitControls(); });
    $("portrait-character-list").append(button);
  }
  picker.value = choices.has(previous) ? previous : choices.keys().next().value;
  updatePortraitControls();
  $("portrait-dialog").showModal();
}

function savePortraitCalibration(reset = false) {
  const selected = selectedPortrait();
  if (!selected) return;
  const key = portraitPoseKey(selected.actor, selected.asset), next = { ...portraitAdjustments };
  if (reset) delete next[key];
  else next[key] = { scale: clamp(Number($("portrait-scale").value) || 100, 70, 140),
    y: clamp(Number($("portrait-y").value) || 0, -20, 20) };
  if (!store.put("portraitAdjustments", next)) { updatePortraitControls(); return notify("Could not save portrait alignment. Check browser local storage."); }
  if (reset) delete portraitAdjustments[key]; else portraitAdjustments[key] = next[key];
  updatePortraitControls();
  // Redraw art only: no engine.next(), transcript, bookmark or audio changes.
  renderActors(engine.state, current.assets.resolved, ++renderGeneration);
}

function syncDialogueVisibility() {
  const available = !!current && !!engine && !textOnly && current.story.resourceKind !== "messagecard";
  if (!available) dialogueHidden = false;
  $("stage-shell").classList.toggle("dialogue-hidden", dialogueHidden);
  $("hide-text-button").disabled = !available;
  ui.text($("hide-text-button"), dialogueHidden ? "Show text" : "Hide text");
  $("hide-text-button").setAttribute("aria-pressed", String(dialogueHidden));
}
function setDialogueHidden(hidden) {
  dialogueHidden = !!hidden;
  if (dialogueHidden) setAuto(false);
  syncDialogueVisibility();
}
function restoreDialogue() {
  if (!dialogueHidden) return false;
  setDialogueHidden(false);
  return true;
}
function preventSurfaceSelection(event) {
  if (!current || current.theaterMedia || current.story.resourceKind === "messagecard") return;
  if (event.target.closest("button, a, input, select, textarea, [role=button]")) return;
  event.preventDefault();
}
function applyProgressReset() {
  setAuto(false); stopAudio();
  if ($("log-dialog").open) $("log-dialog").close();
  dialogueHidden = false;
  if (current?.theaterMedia) { $("theater-video").pause?.(); $("theater-video").currentTime = 0; $("theater-video").dataset.resumeTime = "0"; saveTheaterPosition(); }
  if (current && engine) {
    engine = new StoryEngine(current.script.commands, renderState);
    engine.next();
  }
  syncDialogueVisibility();
}
function resetAllReadingProgress() {
  const failure = message => {
    ui.text($("reset-progress-status"), message);
    $("reset-progress-status").hidden = false;
    notify(message); return false;
  };
  if (editIsDirty() || translationEdit?.busy) {
    return failure("Save or discard your personal revision before resetting progress.");
  }
  let cleared = 0;
  try {
    const keys = [];
    for (let index = 0; index < localStorage.length; index++) {
      const key = localStorage.key(index);
      if (/^fkg-reader:progress:(?:[0-9]+|date:[0-9]{6}:100|message:[1-9][0-9]*|theater:[1-9][0-9]{0,9})$/.test(key || "")) keys.push(key);
    }
    for (const key of keys) { localStorage.removeItem(key); cleared++; }
  } catch {
    return failure(cleared ? "Some positions were cleared, but the browser blocked the remaining reset. Read marks are unchanged."
      : "Could not reset reading progress. Check browser local storage.");
  }
  // Only bookmark keys are removed. Read marks, favorites, glossary, personal
  // revisions and settings are separate; never call localStorage.clear().
  const broadcast = store.put("progressResetAt", `${Date.now()}-${Math.random()}`);
  applyProgressReset();
  $("reset-progress-dialog").close();
  notify(broadcast ? "Reading progress reset. Read marks and personal settings kept."
    : "Progress reset in this tab; other open tabs could not be notified.");
  return true;
}
function bindNumericSetting(id, min, max, onCommit) {
  const number = $(`${id}-number`), slider = $(id);
  const commit = () => {
    const raw = String(number.value).trim(), value = Number(raw);
    if (!raw || !Number.isFinite(value)) { number.value = String(slider.value); return; }
    slider.value = String(Math.round(clamp(value, min, max)));
    onCommit();
  };
  number.addEventListener("change", commit);
  // Also support programmatic/autofill edits that do not dispatch change when
  // focus leaves the field. Skip the duplicate commit after a normal change.
  number.addEventListener("blur", () => { if (String(number.value) !== String(slider.value)) commit(); });
  number.addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); commit(); }
  });
}
function advanceFromSurface(event) {
  if (current?.story.resourceKind === "messagecard") return;
  if (!engine || !current || event.defaultPrevented || event.button > 0 || event.detail > 1) return;
  if (document.querySelector("dialog[open]")) return;
  if (event.target.closest("button, a, input, select, textarea, [role=button], .choices")) return;
  if (restoreDialogue()) return;
  if (engine.state.choice || engine.state.ended) return;
  engine.next();
}

$("advance").addEventListener("click", () => { if (!restoreDialogue()) engine?.next(); });
$("stage-shell").addEventListener("click", advanceFromSurface);
for (const type of ["selectstart", "dragstart"]) $("stage-shell").addEventListener(type, preventSurfaceSelection);
$("hide-text-button").addEventListener("click", () => setDialogueHidden(!dialogueHidden));
$("library-button").addEventListener("click", openLibrary);
$("log-button").addEventListener("click", showLog);
$("log-dialog").addEventListener("close", stopLogVoice);
$("log-audio-button").addEventListener("click", () => toggleSound(true));
$("log-search").addEventListener("input", renderLog);
$("log-bottom").addEventListener("click", () => { $("log-list").scrollTop = $("log-list").scrollHeight; });
$("portrait-settings").addEventListener("click", showPortraitCalibration);
$("portrait-character").addEventListener("change", updatePortraitControls);
for (const id of ["portrait-scale", "portrait-y"]) $(id).addEventListener("input", () => savePortraitCalibration());
bindNumericSetting("portrait-scale", 70, 140, () => savePortraitCalibration());
bindNumericSetting("portrait-y", -20, 20, () => savePortraitCalibration());
$("portrait-reset").addEventListener("click", () => savePortraitCalibration(true));
$("previous").addEventListener("click", () => { setAuto(false); if (!restoreDialogue()) engine?.previous(); });
$("restart").addEventListener("click", () => {
  if (current?.theaterMedia?.url) { $("theater-video").pause?.(); $("theater-video").currentTime = 0; $("theater-video").dataset.resumeTime = "0"; saveTheaterPosition(); }
  else if (current) openStory(current.story.id, true);
});
$("auto").addEventListener("click", () => setAuto(!autoMode));
$("first-story").addEventListener("click", openLibrary);
$("read-button").addEventListener("click", () => {
  if (!current) return;
  const id = String(current.story.id);
  const marked = !readStories.has(id);
  const next = new Set(readStories);
  if (marked) next.add(id); else next.delete(id);
  if (!store.put("readStories", [...next])) {
    notify("Could not save read status. Check browser local storage.");
    return;
  }
  if (marked) readStories.add(id); else readStories.delete(id);
  updateReadButton();
  const scrollTop = $("story-list").scrollTop;
  const scrollLeft = $("story-list").scrollLeft;
  renderCards();
  $("story-list").scrollTop = scrollTop;
  $("story-list").scrollLeft = scrollLeft;
  notify(marked ? "Marked as read" : "Marked as unread; reading position kept");
});
$("favorite-button").addEventListener("click", () => {
  if (!current) return;
  rememberCatalog();
  const id = current.story.id;
  if (favorites.has(id)) favorites.delete(id); else favorites.add(id);
  store.put("favorites", [...favorites]);
  $("favorite-button").textContent = favorites.has(id) ? "★" : "☆";
  if (favoriteOnly) loadCards(); else { renderCards(); restoreCatalog(); }
});
$("mode-button").addEventListener("click", () => { textOnly = !textOnly; store.put("textOnly", textOnly); setMode(); });
$("audio-button").addEventListener("click", () => toggleSound());
$("font-size").value = clamp(Number(store.get("fontSize", 21)) || 21, 12, 30);
function setFont() {
  const size = clamp(Number($("font-size").value) || 21, 12, 30);
  $("font-size").value = size;
  $("font-size-number").value = String(size);
  $("font-size-value").textContent = "px";
  document.documentElement.style.setProperty("--message-size", `${size}px`);
  if (!store.put("fontSize", size)) notify("Font size applied, but this browser could not save it.");
}
$("font-size").addEventListener("input", setFont); setFont();
bindNumericSetting("font-size", 12, 30, setFont);
bindNumericSetting("dialogue-height", 140, 480, () => updateReadingPreferences({ height: Number($("dialogue-height").value) }));
bindNumericSetting("dialogue-opacity", 0, 100, () => updateReadingPreferences({ opacity: Number($("dialogue-opacity").value) }));
$("reset-font-size").addEventListener("click", () => { $("font-size").value = 21; setFont(); });
$("reading-settings-button").addEventListener("click", showReadingSettings);
$("messagecard-edit").addEventListener("click", () => {
  const command = current?.messageCard && current.script.commands.find(command => command.op === "mess");
  if (command) showTranslationEditor(`mess:${command.line}:text`, messageCardSource());
});
$("log-reading-settings").addEventListener("click", showReadingSettings);
$("open-glossary").addEventListener("click", showGlossary);
$("open-revisions").addEventListener("click", showRevisionManager);
$("edit-translation").addEventListener("click", () => {
  if (engine?.state.message) showTranslationEditor(`mess:${engine.state.message.line}:text`, engine.state.message.text);
});
$("translation-edit-form").addEventListener("submit", event => { event.preventDefault(); return saveTranslationEdit(); });
$("translation-edit-reset").addEventListener("click", () => saveTranslationEdit(true));
$("translation-edit-text").addEventListener("input", previewTranslationEdit);
$("translation-editor-close").addEventListener("click", () => closeTranslationEditor());
$("translation-editor").addEventListener("cancel", event => { event.preventDefault(); closeTranslationEditor(); });
$("translation-discard").addEventListener("click", () => closeTranslationEditor(true));
$("translation-edit-field").addEventListener("change", () => {
  if (!translationEdit || translationEdit.busy) return;
  if (editIsDirty()) {
    $("translation-edit-field").value = translationEdit.identity;
    ui.text($("translation-edit-status"), "Save your changes before switching fields, or close and discard them."); return;
  }
  fillTranslationEditor($("translation-edit-field").value);
});
$("translation-unlink").addEventListener("click", () => {
  if (!translationEdit || translationEdit.busy) return;
  // Resolve only glossary links; preserve runtime markers such as %user%.
  $("translation-edit-text").value = $("translation-edit-text").value.replace(/\{\{term:([A-Za-z0-9_.:-]+)\}\}/g,
    (token, id) => termOverrides[id] ?? translationEdit.terms[id] ?? token);
  previewTranslationEdit();
});
$("revision-import").addEventListener("click", () => $("revision-file").click());
$("revision-file").addEventListener("change", async event => { await prepareRevisionImport(event.target.files?.[0]); event.target.value = ""; });
$("revision-import-apply").addEventListener("click", applyRevisionImport);
$("settings-glossary").addEventListener("click", showGlossary);
for (const id of ["glossary-search", "glossary-scope"]) $(id).addEventListener(id === "glossary-search" ? "input" : "change", () => { glossaryLimit = 80; renderGlossary(); });
for (const type of ["characters", "other"]) $(`glossary-${type}`).addEventListener("click", () => { glossaryType = type; glossaryLimit = 80; renderGlossary(); });
$("glossary-form").addEventListener("submit", event => { event.preventDefault(); saveTermOverride(glossarySelection, $("glossary-custom").value); });
$("glossary-reset").addEventListener("click", () => saveTermOverride(glossarySelection, ""));
$("reading-language").addEventListener("change", () => updateReadingPreferences({ language: $("reading-language").value }));
$("swap-reading-languages").addEventListener("click", () => updateReadingPreferences({ reversed: !readingPreferences.reversed }));
$("dialogue-height").addEventListener("input", () => updateReadingPreferences({ height: Number($("dialogue-height").value) }));
$("reset-dialogue-height").addEventListener("click", () => updateReadingPreferences({ height: null }));
$("dialogue-opacity").addEventListener("input", () => updateReadingPreferences({ opacity: Number($("dialogue-opacity").value) }));
$("reset-dialogue-opacity").addEventListener("click", () => updateReadingPreferences({ opacity: 85 }));
for (const kind of Object.keys(defaultAudioPreferences)) {
  $(`${kind}-volume`).addEventListener("input", () => updateAudioPreferences(kind, Number($(`${kind}-volume`).value)));
  bindNumericSetting(`${kind}-volume`, 0, 100, () => updateAudioPreferences(kind, Number($(`${kind}-volume`).value)));
}
$("reset-progress").addEventListener("click", () => {
  setAuto(false); $("reset-progress-status").hidden = true; ui.text($("reset-progress-status"), "");
  $("reset-progress-dialog").showModal();
});
$("reset-progress-cancel").addEventListener("click", () => $("reset-progress-dialog").close());
$("reset-progress-confirm").addEventListener("click", resetAllReadingProgress);
$("player-name").value = playerName;
$("player-name-form").addEventListener("submit", event => {
  event.preventDefault();
  const name = normalizePlayerName($("player-name").value);
  if (!store.put("playerName", name)) { notify("Name not saved: this browser could not save settings."); return; }
  playerName = name;
  $("player-name").value = name;
  if (current?.story.resourceKind === "messagecard") {
    renderMessageCardText();
    if ($("log-dialog").open) {
      const top = $("log-list").scrollTop;
      renderLog();
      $("log-list").scrollTop = top;
    }
  }
  notify("Player name saved");
});
$("settings-button").addEventListener("click", () => {
  setAuto(false); pauseTheater(); $("player-name").value = playerName; $("settings-dialog").showModal();
});
$("ui-language").addEventListener("change", () => {
  const language = $("ui-language").value === "zh-Hans" ? "zh-Hans" : "en";
  const saved = store.put("uiLanguage", language);
  rememberCatalog();
  // Repaint UI bindings only: no engine replay, audio restart or input reset.
  ui.setLanguage(language);
  if (status) { renderGroups(); renderCards(); restoreCatalog(); }
  if (current && engine) {
    const originalScroll = $("message").scrollTop, translatedScroll = $("message-translation").scrollTop;
    if (current.story.resourceKind === "messagecard") renderMessageCardText();
    else renderDialogueText(engine.state);
    $("message").scrollTop = originalScroll; $("message-translation").scrollTop = translatedScroll;
  }
  if (!saved) notify("Settings applied, but this browser could not save them.");
});
$("diagnostics").addEventListener("click", showDiagnostics);
for (const button of document.querySelectorAll("[data-close]")) button.addEventListener("click", () => { rememberCatalog(); button.closest("dialog").close(); });
$("library-dialog").addEventListener("cancel", rememberCatalog);
for (const id of ["group-panel", "story-list", "category-rail"]) {
  $(id).addEventListener("scroll", () => {
    if (restoringCatalog.has(id)) return;
    clearTimeout(catalogSaveTimer);
    catalogSaveTimer = setTimeout(rememberCatalog, 150);
  }, { passive: true });
  // A user's wheel/touch/key interaction takes priority over a queued restore.
  for (const type of ["wheel", "touchstart", "pointerdown", "keydown"]) {
    $(id).addEventListener(type, () => restoringCatalog.delete(id), { passive: true });
  }
}
globalThis.addEventListener?.("pagehide", () => { rememberCatalog(); pauseTheater(); });
globalThis.addEventListener?.("storage", event => {
  if (event.key === "fkg-reader:progressResetAt" && event.newValue && event.newValue !== event.oldValue) {
    applyProgressReset(); notify("Reading progress reset in another tab. Read marks kept.");
  }
});
globalThis.addEventListener?.("beforeunload", event => {
  if (editIsDirty() || translationEdit?.busy) { event.preventDefault(); event.returnValue = ""; }
});
document.addEventListener("visibilitychange", () => { if (document.hidden) rememberCatalog(); });
$("refresh").addEventListener("click", async () => {
  try { await api("/api/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); await loadStatus(); await refreshCurrentMetadata(); await loadCards(); notify("Library rescanned"); }
  catch (error) { notify(error.message); }
});
$("import-button").addEventListener("click", () => $("import-file").click());
$("import-file").addEventListener("change", async event => {
  const file = event.target.files[0]; if (!file) return;
  try {
    const payload = JSON.parse((await file.text()).replace(/^\uFEFF/, ""));
    const result = await api("/api/import-metadata", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    await loadStatus(); await refreshCurrentMetadata(); await loadCards();
    notify(result.summary ? `${result.matched} local stories matched; ${result.summary.classified} classified; ${result.summary.titled} titled.` : `${result.imported} catalog entries imported`);
    if (result.summary) {
      const note = makeUI("p", `Import: ${result.matched} local files matched; ${result.warningCount} association notes.`, "muted small");
      $("source-info").append(note);
      if (result.warningCount) $("source-info").append(make("p", result.warnings.slice(0, 3).join("; "), "muted small"));
    }
  } catch (error) { notify(error.message); }
  event.target.value = "";
});
$("export-missing").addEventListener("click", () => {
  if (!current) return;
  const data = { storyId: current.story.id, references: current.script.references, missing: current.assets.missing, warnings: current.script.warnings };
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const link = make("a"); link.href = url; link.download = `${current.story.logicalName}-missing.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
let searchTimer;
$("search").addEventListener("input", () => { rememberCatalog(); listGeneration++; clearTimeout(searchTimer); searchTimer = setTimeout(() => loadCards(), 200); });
$("group-search").addEventListener("input", () => { rememberCatalog(); renderGroups(); saveCatalogSelection(); });
$("category-all").addEventListener("click", async () => {
  rememberCatalog(); clearTimeout(searchTimer);
  favoriteOnly = false; updateLibraryTabs();
  $("category").value = ""; $("search").value = ""; $("group-search").value = "";
  await selectCategory();
});
$("catalog-back").addEventListener("click", () => {
  rememberCatalog(); clearTimeout(searchTimer);
  if (favoriteOnly) return applyCatalogSelection({ ...browseCatalog, favoriteOnly: false });
  favoriteOnly = false; updateLibraryTabs();
  if (!$("group").value && !$("search").value.trim()) {
    selectedPart = "";
    $("group-search").value = catalogPosition(catalogGroupFilterKey()).query || "";
  }
  $("group").value = ""; $("search").value = "";
  renderGroups(); return loadCards();
});
$("category").addEventListener("change", selectCategory);
$("group").addEventListener("change", () => loadCards());
for (const [id, value] of [["all-tab", false], ["favorite-tab", true]]) $(id).addEventListener("click", () => {
  rememberCatalog(); clearTimeout(searchTimer);
  if (!value && favoriteOnly) return applyCatalogSelection({ ...browseCatalog, favoriteOnly: false });
  if (value && !favoriteOnly) { browseCatalog = catalogSnapshot(); store.put("catalogBrowse", browseCatalog); }
  favoriteOnly = value; updateLibraryTabs();
  if (value) { groupGeneration++; $("category").value = ""; $("group").value = ""; $("search").value = ""; $("group-search").value = ""; groups = []; mainParts = []; selectedPart = ""; }
  return loadCards();
});
document.addEventListener("keydown", event => {
  if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
  if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName) || document.querySelector("dialog[open]")) return;
  if (["BUTTON", "A"].includes(document.activeElement.tagName) && [" ", "Enter"].includes(event.key)) return;
  if (event.key === "/") { event.preventDefault(); openLibrary(); $("search").focus(); return; }
  if (event.key.toLowerCase() === "l" && engine) { event.preventDefault(); showLog(); return; }
  if (current?.story.resourceKind === "messagecard") return;
  if (event.key.toLowerCase() === "h" && engine && !textOnly) { event.preventDefault(); setDialogueHidden(!dialogueHidden); return; }
  if (event.key === "Escape" && dialogueHidden) { event.preventDefault(); restoreDialogue(); return; }
  if ([" ", "ArrowRight", "Enter"].includes(event.key) && engine) { event.preventDefault(); if (!restoreDialogue()) engine.next(); }
  if (event.key === "ArrowLeft" && engine) { event.preventDefault(); setAuto(false); if (!restoreDialogue()) engine.previous(); }
});

setMode();
try {
  await loadStatus();
  await applyCatalogSelection(savedCatalog);
  const last = store.get("lastStory", null);
  if (last && !loadGeneration) {
    // Old bookmarks may point at a category removed from the visible catalog.
    const available = await api(`/api/stories?${new URLSearchParams({ ids: String(last), limit: "1" })}`);
    if (available.total && !loadGeneration) await openStory(last, false, false);
  }
  if (!current) openLibrary();
} catch (error) { notify(error.message); ui.text($("story-total"), "Could not load library"); }
