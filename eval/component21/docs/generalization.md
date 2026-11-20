# Generalization

The development suite and the sealed held-out suite ask whether behavior on the `retry_count` listing transfers. On this baseline, it does not transfer to supported claims, and the correct-looking rejections are not evidence of semantic understanding.

## Development

The generator builds 17 cases from one program about `quota_left` and `release_permit`. It does not read the held-out file. Transformations are identity, rename, paraphrase, comment reorder, whitespace, negation, numeric magnitude, reversed call order, reversed cause, both conditional directions, JavaScript, Go, Rust, fullwidth punctuation, removed evidence, and conflicting evidence.

Supported claims failed `not_presented` for the identity baseline, the rename, the paraphrase, comment reorder, whitespace, and all three languages. The checker did not keep a supported sentence once the identifiers left the regression vocabulary, including the identity case that only changes the words.

Negation, magnitude, reversed order, reversed cause, both conditionals, fullwidth punctuation, removed evidence, and the conflicting pair are vacuous under scorer `integrity-2`. Those sentences were not presented. The regression suite, using `increments`, admitted the negation, the false magnitude, the reversed order, and both causal directions. The development results therefore do not show that those transformations were understood.

The conflicting-evidence case means the combined sentence was not presented. It does not show that both outputs were read and composed.

## Held-out v1

Six cases. Labels still need a second human review (`independent_second_review` is false). Do not tune a replacement verifier on this file.

| Case | Result | Reading |
|---|---|---|
| Rust supported (`ledger_slots` committed before `commit_batch`) | fail, not presented | A supported claim in a new vocabulary was rejected. |
| Rust reversed order | vacuous, not presented | The false order was not admitted. A skip is not a semantic refusal. |
| JavaScript decrement | fail, not presented | The supported decrement was rejected. |
| JavaScript negation | vacuous, not presented | Same caveat as the development negation. |
| Go guard | vacuous, not presented | `depth decreases whenever pop is called` was not presented. The listing does not support that unconditional claim. |
| Mixed Spanish tail | vacuous, not presented | The whole sentence was not presented. Unlike the regression unicode case, this case did not also require the English prefix to be stored alone, and the whole sentence was not admitted. |

Held-out recall of supported claims in this run is 0/2. That is a transfer failure. It is not a license to edit the gold until the daemon passes.
