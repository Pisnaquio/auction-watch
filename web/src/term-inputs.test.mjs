import test from "node:test";
import assert from "node:assert/strict";

import { editTermInput, parseTermInput } from "./term-inputs.js";

test("keeps spaces and an unfinished comma while editing", () => {
  const editing = editTermInput("mesa de pool, ping pong, ");

  assert.equal(editing.text, "mesa de pool, ping pong, ");
  assert.deepEqual(editing.terms, ["mesa de pool", "ping pong"]);
});

test("parses phrases, accents and repeated separators only when saving", () => {
  assert.deepEqual(
    parseTermInput(" biblioteca de autor , edición limitada,, réplica "),
    ["biblioteca de autor", "edición limitada", "réplica"],
  );
});
