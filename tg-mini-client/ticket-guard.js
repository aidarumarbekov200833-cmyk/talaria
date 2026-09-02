"use strict";

// Одноразовость билетов /tg-connect (см. комментарий у использования в
// server.js). Вынесено в модуль ради тестов: server.js при require поднимает
// сервер и в node --test не загружается.

class TicketGuard {
  constructor() {
    this.used = new Map(); // sig -> exp (unix seconds)
  }

  /** Сжечь билет. true — первый раз, false — уже использован или без подписи. */
  consume(token) {
    const parts = String(token || "").split(".");
    const sig = parts[4];
    const exp = Number(parts[3]) || 0;
    if (!sig) return false;
    if (this.used.has(sig)) return false;
    this.used.set(sig, exp);
    return true;
  }

  /** Забыть билеты, срок которых вышел: их и так не примет readToken. */
  prune(nowSec = Math.floor(Date.now() / 1000)) {
    for (const [sig, exp] of this.used) {
      if (exp < nowSec) this.used.delete(sig);
    }
    return this.used.size;
  }
}

module.exports = { TicketGuard };
