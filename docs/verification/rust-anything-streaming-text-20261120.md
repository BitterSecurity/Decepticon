# Rust parity experiment for streaming text

The TypeScript `stripResultTags` formatter in
[`clients/shared/streaming/src/utils.ts`](../../clients/shared/streaming/src/utils.ts)
matched a Rust implementation on all 231 recorded cases. The Node native
binding measured 899 ns per call versus 76 ns for TypeScript, so the
experiment recommends keeping the existing implementation.

The run used rust-anything 2.0.2, Cargo 1.97.1, Node 24.13.0, Linux x86-64,
and baseline `be0892d55187c2ebb9ee24109020558241eaab14`. The benchmark
includes the Node-to-Rust call and used rust-anything's default 200 ms
minimum sample time per side. These numbers describe this local run and
this corpus. They do not measure browser or whole-application performance.

The Rust implementation scanned the original string in order, skipped
only the two literal tags, and trimmed the ECMAScript whitespace set.
The corpus included Korean text, emoji, byte-order marks, nonbreaking
spaces, and two Unicode characters that JavaScript deliberately leaves
untrimmed. It also checked that removing a tag does not cause a newly
formed tag to be removed in a second pass. For example,
`</re<result>sult>` becomes `</result>`.

`rust-anything diff` and `rust-anything gate` both matched 231 of 231
cases. The latter also passed formatting, Clippy, Cargo test, native
build, and the export check. Coverage was 17 of 30 lines in the file
because the experiment covered only `stripResultTags`. The file also
exports `extractText`, which stayed in TypeScript. The cases do not
establish parity for isolated UTF-16 surrogates or for the application
as a whole.

To reproduce the tool setup, use a disposable output directory and
Rust 1.97 or later. Run the following from the repository root. Then
implement the function in the generated `src/port.rs`, record with
`--inputs` for the edge cases above, and run `build`, `diff`, `gate`,
and `bench` on that crate. Local Cargo builds and gates use the shared
build slot.

```sh
rust-anything bridge clients/shared/streaming src/utils.ts \
  --kind napi --keep 'extractText: dynamic JavaScript coercion' \
  --name streaming-text-rs --out /tmp/streaming-text-rs
```
