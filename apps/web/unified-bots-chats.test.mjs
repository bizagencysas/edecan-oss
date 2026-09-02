import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(path) {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

test("bots unifica chats 1:1 y grupos en web e iOS", () => {
  const botsPage = source("./src/app/(app)/app/bots/page.tsx");
  const teamsPage = source("./src/app/(app)/app/teams/page.tsx");
  const nav = source("./src/components/layout/nav-items.ts");
  const root = source("../mobile/ios/EdecanApp/RootTabView.swift");
  const list = source("../mobile/ios/EdecanApp/Screens/BotsChatsView.swift");

  assert.match(botsPage, /kind: "bot"/);
  assert.match(botsPage, /kind: "team"/);
  assert.match(botsPage, /createTeam/);
  assert.match(teamsPage, /router\.replace\(target\)/);
  assert.match(teamsPage, /\/app\/bots/);
  assert.match(nav, /href: "\/app\/bots", label: "Bots"/);
  assert.doesNotMatch(nav, /href: "\/app\/teams"/);
  assert.match(root, /BotsChatsView/);
  assert.match(list, /BotChatView/);
  assert.match(list, /TeamConversationView/);
});
