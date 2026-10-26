# CLI experience and compatibility

The terminal has four distinct concepts: the engagement workspace, the selected
Plan/Red/Blue conversation, the current run state, and independent Blue monitoring.
Red is the default conversation; scope readiness and approval remain execution
requirements. Switching conversation does not turn off Blue collection.

## Interaction design references

The command picker, composer, session and monitoring changes use the following
public interaction references:

- [Claude Code interactive mode](https://code.claude.com/docs/en/interactive-mode):
  input editing, command discovery, queued input and transcript expansion.
- [Codex CLI](https://learn.chatgpt.com/docs/codex/cli): visible session state and
  explicit session restoration.
- [Gemini session management](https://geminicli.com/docs/cli/session-management/):
  project-scoped conversations and restoration.
- [OpenCode TUI](https://docs.opencode.ai/docs/tui/): searchable actions and external
  editor integration.

The implementation retains React/Ink and the Go Charm launcher. React Bits was
evaluated as a web component reference; it is not a terminal renderer dependency.
No browser animation library is required for the CLI.

## Default keyboard policy (reference review: 2026-10-20)

Reviewed the official [Claude Code editing reference](https://code.claude.com/docs/en/interactive-mode),
[Claude keybindings](https://code.claude.com/docs/en/keybindings),
[Codex interactive commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli),
and [Gemini keyboard reference](https://geminicli.com/docs/reference/keyboard-shortcuts/).
Local reference binaries reported Claude Code 2.1.303 and Codex 0.163.0; this does
not assert that local custom keymaps or terminal settings are their defaults.

| Input task | Decepticon default and reference |
|---|---|
| Send / multiline | Enter sends; Shift+Enter, Alt+Enter, Ctrl+J or backslash-Enter inserts a line (Claude) |
| Navigation | Arrow keys and Home/End work directly; Ctrl+A/E remain line-navigation aliases (Claude/Gemini) |
| History / discovery | Up/Down, Ctrl+R, slash completion, Tab acceptance (Claude/Codex) |
| Word editing | Ctrl+arrows and Alt+B/F; modified Backspace/Delete and Alt+D (Claude/Gemini) |
| Recover edits | Ctrl+Y pastes the last cut; Ctrl+_ undoes an input edit (Claude) |
| Help / model | Empty `?` shows help; Alt+P opens the configured model picker (Claude) |
| Interrupt | Esc pauses active work after dismissing focused UI; queued messages survive |
| Exit | Empty idle Ctrl+D requires two presses; text retains forward-delete behavior (Claude) |

Ctrl+A follows terminal line editing rather than GUI Select All. The visible
footer leads with arrows and Shift+Enter, and `?` and `/help` share one shortcut
list. Unicode word navigation preserves dictionary segmentation while splitting
path punctuation; Ctrl+W removes a whole whitespace-delimited token.

Where products differ, Decepticon follows Claude's Ctrl+O transcript behavior;
Codex's current documentation assigns it to copying a response. Decepticon also
retains its existing checkpoint pause/resume and explicit second-Ctrl+C hard
cancel. Esc uses the existing checkpoint-preserving interrupt operation. These
bindings do not change scope, authorize an attack, map permission modes to
Red/Blue, or implement conversation rewind. Native terminal selection and
terminal-owned clipboard shortcuts continue to belong to the terminal.

[Ink 6.8.0](https://github.com/vadimdemedes/ink/tree/v6.8.0) supplies the existing
renderer and enhanced-key negotiation. No new input framework is added. Portable
newline fallbacks remain available when a terminal cannot distinguish modifiers.

## Upgrade behavior

The keyboard and slash command changes are merged into `main` and tracked under
Unreleased in [version history](../CHANGELOG.md). They require a launcher/CLI image
and, where applicable, Compose configuration containing the changes. Existing
released images do not acquire the features simply because the source branch has
changed. No release version is assigned here.

The CLI Compose service mounts the selected engagement at `/workspace`, consistent
with the agent workspace. Drafts, prompt history and Blue cursors prefer the
persisted `DECEPTICON_HOME` mount, with `DECEPTICON_STATE_DIR` as an explicit
override. Existing database and engagement volumes do not need resetting.

Old conversations without engagement metadata are kept and discoverable using
`/sessions legacy`. Explicitly link them to the correct engagement before resuming;
checkpoint scope conflicts are rejected. The new metadata does not move files or
change the underlying target authorization.

Blue summaries are compact by default. `/alerts` opens recent structured notices,
and `/alerts <incident-id>` shows the complete retained investigation. Up to 500
notices are retained in the current UI process. The monitor remains the durable
incident store. The persisted cursor avoids replaying every previously seen alert
on re-entry. It is distinct from whether the user has reviewed an incident.

Leaving the CLI keeps services running. `decepticon stop` is the explicit host
command for shutdown. `/status` and read-only `decepticon doctor --json` expose
connection state without changing it.

See the [CLI reference](cli-reference.md) for commands and keyboard controls.
