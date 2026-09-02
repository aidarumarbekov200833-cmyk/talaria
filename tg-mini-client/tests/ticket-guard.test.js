"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { TicketGuard } = require("../ticket-guard");

const t = (sig, exp) => `1.2.1.${exp}.${sig}`;

test("билет принимается один раз", () => {
  const g = new TicketGuard();
  assert.equal(g.consume(t("aaa", 9999999999)), true);
  assert.equal(g.consume(t("aaa", 9999999999)), false);
  assert.equal(g.consume(t("bbb", 9999999999)), true);
});

test("мусор без подписи не принимается и не засоряет память", () => {
  const g = new TicketGuard();
  assert.equal(g.consume(""), false);
  assert.equal(g.consume("1.2.3"), false);
  assert.equal(g.consume(null), false);
  assert.equal(g.used.size, 0);
});

test("prune удаляет только протухшие", () => {
  const g = new TicketGuard();
  g.consume(t("old", 100));
  g.consume(t("live", 1000));
  assert.equal(g.prune(500), 1);
  assert.equal(g.used.has("live"), true);
  assert.equal(g.consume(t("old", 100)), true, "после prune подпись свободна — readToken её всё равно отвергнет по exp");
});
