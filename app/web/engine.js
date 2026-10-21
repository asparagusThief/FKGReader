// Independent command interpreter. Handlers can be extended without UI changes.
const portraitScale = value => Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : 1;

export class StoryEngine {
  static choiceFieldId(choice, index) {
    const items = choice.items || choice.data?.list || [], item = items[index];
    if (!item) return null;
    const ambiguous = items.some(item => !item?.element || items.filter(other => other?.element === item.element).length !== 1);
    return ambiguous ? `choice:${choice.line}:option:${index}:text` : `choice:${choice.line}:${item.element}:text`;
  }

  static selectedChoiceIndex(state, choice, log = []) {
    const explicit = state.choiceIndexes?.[choice.line];
    const selected = state.selections?.[choice.line];
    if (Number.isInteger(explicit) && choice.items[explicit]?.element === selected) return explicit;
    const recorded = log.findLast(entry => entry.kind === "choice" && entry.line === choice.line);
    if (recorded) {
      const exact = choice.items.findIndex(item => item.element === selected && (item.text || "Continue").replaceAll("\\n", "\n") === recorded.text);
      if (exact >= 0) return exact;
    }
    return choice.items.findIndex(item => item.element === selected);
  }
  constructor(commands, onChange) {
    this.commands = commands;
    this.onChange = onChange;
    this.handlers = new Map();
    this.reset(false);
    this.installDefaults();
  }

  reset(emit = true) {
    this.pc = 0;
    this.history = [];
    this.log = [];
    this.logged = new Set();
    this.state = {
      actors: {}, background: "", bgm: "", voice: "", se: "", seSerial: 0,
      message: null, messageNumber: 0, choice: null, selected: "", selections: {},
      windowVisible: true, ended: false, skipped: {},
    };
    if (emit) this.onChange(this.state);
  }

  snapshot() {
    return { pc: this.pc, state: structuredClone(this.state) };
  }

  restore(snapshot, log) {
    if (!snapshot || !Number.isInteger(snapshot.pc) || snapshot.pc < 0 || snapshot.pc > this.commands.length || !snapshot.state?.actors) return false;
    this.pc = snapshot.pc;
    this.state = structuredClone(snapshot.state);
    const validLog = Array.isArray(log) && log.every(entry => entry && ["message", "choice"].includes(entry.kind) && typeof entry.key === "string" && typeof entry.text === "string" && typeof entry.name === "string");
    const upgradeActors = Object.values(this.state.actors).some(actor => !Number.isFinite(actor.scale) || !Number.isFinite(actor.originY));
    let replay;
    if (!validLog || upgradeActors) {
      // Reconstruct only the already-visited branch, without rendering, audio
      // or storage writes. Older bookmarks lack the new portrait transforms.
      replay = new StoryEngine(this.commands, () => {});
      let budget = this.commands.length + 1;
      while (replay.pc < snapshot.pc && !replay.state.ended && budget-- > 0) {
        if (replay.state.choice) {
          const index = StoryEngine.selectedChoiceIndex(this.state, replay.state.choice, validLog ? log : []);
          if (index < 0) break;
          replay.choose(index);
        } else replay.next();
      }
    }
    if (upgradeActors) {
      for (const [name, actor] of Object.entries(this.state.actors)) {
        const restored = replay?.pc === snapshot.pc ? replay.state.actors[name] : null;
        const sameActor = restored?.file === actor.file;
        if (!Number.isFinite(actor.scale)) actor.scale = sameActor ? restored.scale : 1;
        if (!Number.isFinite(actor.originY)) actor.originY = sameActor ? restored.originY : Number(actor.y) || 0;
      }
    }
    if (validLog) {
      this.log = structuredClone(log);
    } else {
      this.log = replay.log;
    }
    // Exact old text only: preserve bookmark PC and append-only History while
    // upgrading the reviewed physical-line continuations, never arbitrary logs.
    for (const command of this.commands) {
      const before = command.sourceCompatibility?.previousMessage;
      if (command.op !== "mess" || !before) continue;
      if (this.state.message?.line === command.line && this.state.message.name === before.name && this.state.message.text === before.text) {
        this.state.message.text = command.text;
        if (this.state.voice === before.voice) this.state.voice = command.voice;
      }
      for (const entry of this.log) if (entry.kind === "message" && entry.line === command.line && entry.name === before.name && entry.text === before.text) {
        entry.text = command.text; entry.voice = command.voice || "";
      }
    }
    for (const entry of this.log) {
      if (entry.kind !== "choice" || entry.sourceIdentity) continue;
      const command = this.commands.find(command => command.op === "choice" && command.line === entry.line);
      if (!command) continue;
      const items = command.data?.list || [];
      const matches = items.flatMap((item, index) => entry.key === `choice:${command.line}:${item.element}`
        && (item.text || "Continue").replaceAll("\\n", "\n") === entry.text ? [index] : []);
      if (matches.length !== 1) continue;
      const identity = StoryEngine.choiceFieldId(command, matches[0]);
      if (identity.includes(":option:")) Object.assign(entry, { key: identity.slice(0, -5), sourceIdentity: identity, choiceIndex: matches[0] });
    }
    this.logged = new Set(this.log.map(entry => entry.key));
    this.history = [this.snapshot()];
    this.onChange(this.state);
    return true;
  }

  register(op, handler) { this.handlers.set(op, handler); }

  recordLog(entry) {
    if (this.logged.has(entry.key)) return;
    this.logged.add(entry.key);
    this.log.push(entry);
  }

  installDefaults() {
    this.register("mess", command => {
      this.state.message = { name: command.name, text: command.text, line: command.line };
      this.state.voice = command.voice || "";
      this.state.messageNumber++;
      this.state.windowVisible = true;
      this.recordLog({ kind: "message", key: `message:${command.line ?? this.pc}`, name: command.name || "", text: command.text || "", line: command.line, voice: command.voice || "" });
      return "pause";
    });
    for (const op of ["background", "bgm"]) {
      this.register(op, command => { this.state[op] = command.args[0] || ""; });
    }
    this.register("se", command => { this.state.se = command.args[0] || ""; this.state.seSerial++; });
    this.register("story_chara", command => {
      const [file, name, position, , x, y] = command.args;
      this.state.actors[name] = { name, file, position: Number(position || 0), x: Number(x || 0),
        y: Number(y || 0), originY: Number(y || 0), scale: portraitScale(command.args[11]), reverse: 1, emotion: "" };
    });
    this.register("story_chara_exit", command => { delete this.state.actors[command.args[0]]; });
    this.register("chara_motion", command => {
      for (const item of command.data?.list || []) {
        const name = item.name || "";
        if (item.action === "out") { delete this.state.actors[name]; continue; }
        if (item.action === "in" && item.fileName) {
          this.state.actors[name] = { name, file: item.fileName, position: { center: 0, left: 1, right: 2 }[item.position] ?? 0,
            x: Number(item.offsetX || 0), y: Number(item.offsetY || 0), originY: Number(item.offsetY || 0),
            scale: portraitScale(item.scale), reverse: 1, emotion: "" };
          continue;
        }
        const actor = this.state.actors[name];
        if (!actor) continue;
        if (item.fileName) actor.file = item.fileName;
        if (item.action === "reverse") actor.reverse = Number(item.reverse || 1);
        if (item.action === "scale") actor.scale = portraitScale(item.scale);
        if (item.action === "move") {
          actor.x += Number(item.offsetX || 0);
          actor.y += Number(item.offsetY || 0);
        }
      }
    });
    this.register("emotion", command => { this.state.emotion = command.data?.emotion || ""; });
    this.register("message_window", command => { this.state.windowVisible = command.args[0] !== "0"; });
    this.register("choice", command => {
      const items = command.data?.list || [];
      if (!items.length) return;
      this.state.choice = { line: command.line, items };
      return "pause";
    });
    this.register("answer", command => {
      const [element, action] = command.args;
      if (action !== "start" || element === this.state.selected) return;
      // Skip the unselected answer block rather than displaying all branches.
      let depth = 1;
      while (this.pc < this.commands.length && depth) {
        const next = this.commands[this.pc++];
        if (next.op === "answer" && next.args[0] === element) {
          if (next.args[1] === "start") depth++;
          if (next.args[1] === "end") depth--;
        }
      }
    });
    for (const op of ["label", "wait", "effect", "overlay_effect", "story_chara_effect", "message_window_position", "full_skip_dialog"]) {
      this.register(op, () => {});
    }
  }

  next() {
    if (this.state.choice || this.state.ended) return;
    let budget = this.commands.length + 1;
    while (this.pc < this.commands.length && budget-- > 0) {
      const command = this.commands[this.pc++];
      const handler = this.handlers.get(command.op);
      if (!handler) { this.state.skipped[command.op] = (this.state.skipped[command.op] || 0) + 1; continue; }
      if (handler(command) === "pause") {
        this.history.push(this.snapshot());
        this.onChange(this.state);
        return;
      }
    }
    this.state.ended = true;
    this.onChange(this.state);
  }

  choose(index) {
    const choice = this.state.choice;
    const item = choice?.items[index];
    if (!item) return;
    this.state.selected = item.element;
    const identity = StoryEngine.choiceFieldId(choice, index), special = identity.includes(":option:");
    this.recordLog({ kind: "choice", key: identity.slice(0, -5), name: "", text: (item.text || "Continue").replaceAll("\\n", "\n"), line: choice.line,
      ...(special ? { sourceIdentity: identity, choiceIndex: index } : {}) });
    this.state.selections[choice.line] = item.element;
    if (special) (this.state.choiceIndexes ??= {})[choice.line] = index;
    this.state.choice = null;
    this.next();
  }

  previous() {
    if (this.state.ended) {
      this.state.ended = false;
      if (this.history.length) {
        const last = this.history.at(-1);
        this.pc = last.pc;
        this.state = structuredClone(last.state);
      }
    } else if (this.history.length > 1) {
      this.history.pop();
      const last = this.history.at(-1);
      this.pc = last.pc;
      this.state = structuredClone(last.state);
    }
    this.onChange(this.state);
  }
}
