"use strict";

let state = null;
let current = null;
let busy = false;

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child != null) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function shortName(name) {
  return name.replace(/^\d\d \w+(?: \[[^\]]*\])* - /, "").replace(/\.jar$/i, "");
}

function neededBy(names) {
  const short = names.map(shortName);
  const shown = short.length > 3 ? `${short.slice(0, 3).join(", ")} +${short.length - 3} more` : short.join(", ");
  return el("span", { class: "why", title: short.join("\n") }, "needed by " + shown);
}

function groupLabel(mods) {
  const words = new Set();
  for (const mod of mods) {
    const found = /^\d\d (\w+)/.exec(mod.name);
    if (found && found[1] !== "lib") words.add(found[1]);
  }
  return words.size ? [...words].join(", ") : "other";
}

function notice(text, kind) {
  const box = $("notice");
  box.className = "banner " + kind;
  box.replaceChildren(el("span", {}, text), el("button", { class: "ghost small", onclick: () => (box.hidden = true) }, "OK"));
  box.hidden = !text;
}

async function load(refresh = false) {
  const next = refresh ? await api().refresh() : await api().state();
  if (next.error && !next.packs) {
    notice(next.error, "error");
    return;
  }
  state = next;
  if (!state.packs.some((p) => p.id === current)) current = state.packs.length ? state.packs[0].id : null;
  render();
}

async function run(call, label) {
  if (busy) return;
  busy = true;
  $("busy-text").textContent = label;
  $("busy").hidden = false;
  render();
  try {
    const started = await call();
    if (started.error) {
      notice(started.error, "error");
      return;
    }
    let status = { done: false };
    while (!status.done) {
      await new Promise((resolve) => setTimeout(resolve, 300));
      status = await api().job(started.job);
    }
    if (status.error) notice(status.error, "error");
    else notice(summary(status.result), "ok");
  } finally {
    busy = false;
    $("busy").hidden = true;
    await load();
  }
}

function summary(result) {
  if (!result) return "Done.";
  if (result.version) return `Updated to ${result.version}, restarting...`;
  if ("pushed" in result) {
    return `Published ${result.entries} files and ${result.configs} configs` + (result.pushed ? " and pushed." : ", nothing new to push.");
  }
  if (result.tagged) return result.tagged.length ? `Tagged: ${result.tagged.join(", ")}.` : "Nothing to tag.";
  const parts = [];
  const count = (key, word) => result[key] && result[key].length && parts.push(`${result[key].length} ${word}`);
  count("added", "added");
  count("updated", "updated");
  count("removed", "removed");
  count("enable", "turned on");
  count("disable", "turned off");
  count("configs", "configs written");
  count("hidden", "worlds hidden");
  count("restored", "worlds back");
  return parts.length ? `Done: ${parts.join(", ")}.` : "Done.";
}

function renderHeader() {
  $("version").textContent = state ? "v" + state.version : "";
  const badges = [];
  if (state && state.offline) badges.push(el("span", { class: "badge warn", title: state.error || "" }, "Offline"));
  $("badges").replaceChildren(...badges);
  $("refresh").disabled = busy;
  $("app-update").hidden = !(state && state.app_update);
  if (state && state.app_update) $("app-version").textContent = state.app_update;
  $("app-apply").disabled = busy;
}

function renderTabs() {
  $("tabs").replaceChildren(
    ...state.packs.map((pack) =>
      el("button", { class: "tab" + (pack.id === current ? " active" : ""), onclick: () => { current = pack.id; render(); } }, pack.title)
    )
  );
}

function changeList(update) {
  const rows = [
    ["added", "New"], ["updated", "Updated"], ["removed", "Removed (kept in backup)"],
    ["enable", "Turn on"], ["disable", "Turn off"], ["configs", "New config files"],
  ];
  return el("ul", { class: "changes" },
    rows.filter(([key]) => update[key].length).map(([key, label]) =>
      el("li", {}, el("b", {}, `${label} (${update[key].length}): `), update[key].map(shortName).join(", "))
    )
  );
}

function updateCard(pack) {
  if (!pack.install) {
    return el("div", { class: "card" },
      el("h2", {}, "Not installed"),
      el("p", {}, `Install ${pack.needs} in TLauncher, then press Refresh.`));
  }
  if (pack.problem) {
    return el("div", { class: "banner warn" }, el("span", {}, pack.problem));
  }
  if (!pack.update) {
    return el("div", { class: "card" }, el("div", { class: "row" },
      el("span", { class: "grow" }, `Up to date - TLauncher version "${pack.install}".`)));
  }
  const title = pack.pending ? "Your changes are ready" : "Update available";
  return el("div", { class: "card" },
    el("div", { class: "row" },
      el("h2", { class: "grow" }, title),
      el("button", { disabled: busy, onclick: () => run(() => api().apply(pack.id), "Applying...") }, "Apply")),
    changeList(pack.update));
}

function modRow(pack, mod) {
  const locked = !mod.choosable || busy;
  const why = mod.side === "required" ? el("span", { class: "badge req" }, "server")
    : mod.needed_by.length ? neededBy(mod.needed_by) : null;
  return el("label", { class: "mod" + (mod.enabled ? "" : " off"), title: mod.name },
    el("input", {
      type: "checkbox", checked: mod.enabled, disabled: locked,
      onchange: async (event) => {
        const result = await api().set_choice(pack.id, mod.name, event.target.checked);
        if (result.error) notice(result.error, "error");
        await load();
      },
    }),
    el("span", { class: "name" }, shortName(mod.name)),
    why);
}

function modsCard(pack) {
  const plain = pack.mods.filter((m) => !m.library);
  const libs = pack.mods.filter((m) => m.library);
  const groups = new Map();
  for (const mod of plain) {
    const key = mod.group == null ? 99 : mod.group;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(mod);
  }
  const sections = [...groups.keys()].sort((a, b) => a - b).map((key) =>
    el("div", { class: "group" },
      el("h3", {}, groupLabel(groups.get(key))),
      groups.get(key).map((mod) => modRow(pack, mod))));
  const on = libs.filter((m) => m.enabled).length;
  return el("div", { class: "card" },
    el("h2", {}, "Mods"),
    el("p", { class: "muted" }, "Server mods are required to join. Uncheck what you do not want, then Apply."),
    sections,
    libs.length ? el("details", {}, el("summary", {}, `Libraries (${on} of ${libs.length} in use)`),
      libs.map((mod) => modRow(pack, mod))) : null,
    pack.own.length ? el("details", {}, el("summary", {}, `Your own mods (${pack.own.length}, left alone)`),
      el("ul", { class: "changes" }, pack.own.map((name) => el("li", {}, name)))) : null);
}

function profileCard(pack) {
  if (!pack.profile) return null;
  const button = (name) => el("button", {
    class: pack.profile === name ? "active" : "ghost", disabled: busy || pack.profile === name,
    onclick: () => run(() => api().switch_profile(name), `Switching to ${name}...`),
  }, name === "full" ? "Full (with friends)" : "Vanilla");
  const rows = pack.worlds.map((world) => el("tr", {},
    el("td", {}, world.name),
    el("td", {}, world.profile || "-"),
    el("td", {}, world.visible ? "visible" : "hidden"),
    el("td", {}, world.profile ? null : ["vanilla", "full"].map((name) =>
      el("button", { class: "ghost small", disabled: busy, onclick: () => run(() => api().assign(name, [world.name]), "Tagging...") }, `tag ${name}`)))));
  return el("div", { class: "card" },
    el("div", { class: "row" }, el("h2", { class: "grow" }, `Profile: ${pack.profile}`), button("vanilla"), button("full")),
    pack.worlds.length ? el("table", {},
      el("tr", {}, el("th", {}, "World"), el("th", {}, "Profile"), el("th", {}, "State"), el("th", {}, "")), rows)
      : el("p", { class: "muted" }, "No worlds yet."));
}

function publishCard(pack) {
  if (!state.owner || !pack.install) return null;
  return el("div", { class: "card" }, el("div", { class: "row" },
    el("span", { class: "grow muted" }, "Owner: write this game dir into the repo and push it to everyone."),
    el("button", { class: "ghost", disabled: busy, onclick: () => run(() => api().publish(pack.id), "Publishing...") }, "Publish")));
}

function render() {
  renderHeader();
  if (!state) return;
  renderTabs();
  const pack = state.packs.find((p) => p.id === current);
  if (!pack) {
    $("pack").replaceChildren(el("p", { class: "muted" }, state.error || "No packs published yet."));
    return;
  }
  $("pack").replaceChildren(...[updateCard(pack), profileCard(pack), modsCard(pack), publishCard(pack)].filter(Boolean));
}

$("refresh").addEventListener("click", () => load(true));
$("app-apply").addEventListener("click", () => run(() => api().update_app(), "Updating the app..."));
window.addEventListener("pywebviewready", () => load());
