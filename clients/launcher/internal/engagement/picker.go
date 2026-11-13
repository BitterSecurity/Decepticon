package engagement

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"charm.land/bubbles/v2/key"
	"charm.land/bubbles/v2/list"
	tea "charm.land/bubbletea/v2"
	"charm.land/huh/v2"
	"charm.land/lipgloss/v2"

	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/ui"
)

const AssistantSoundwave = "soundwave"

const (
	AssistantDecepticon = "decepticon"
	AssistantBlueCell   = "blue_cell"
)

var planningDocuments = []string{
	"roe.json", "threat-profile.json", "conops.json", "deconfliction.json",
	"contact.json", "data-handling.json", "abort.json", "cleanup.json",
}

// Slug regex: lowercase alphanumeric with internal hyphens, 3-64 chars.
var slugRe = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$`)

// Choice carries the picker result back to the launcher.
type Choice struct {
	AssistantID            string
	Engagement             string
	WorkspacePath          string
	Mode                   string
	Target                 string
	AuthorizationConfirmed bool
}

// EngagementEntry pairs a slug with metadata used for picker rendering and
// downstream assistant selection.
type EngagementEntry struct {
	Slug    string
	Ready   bool
	Started bool // run has begun → scope locked (draft while false)
	Scope   scopeSummary
	mtime   int64
}

// scopeSummary is what the picker shows per engagement: the first in-scope
// target, how many entries the machine scope carries, and whether it enforces.
type scopeSummary struct {
	Target    string
	InCount   int
	DenyCount int
	Enforced  bool
}

// readScopeSummary reads the engagement's machine scope for display, accepting
// both the current string-form entries and the pre-unification object-form
// entries; a legacy human-only document still yields its first target.
func readScopeSummary(root string) scopeSummary {
	data, err := os.ReadFile(filepath.Join(root, "plan", "roe.json"))
	if err != nil {
		return scopeSummary{}
	}
	inCount, denyCount, target, ok := readMachineScopeFull(data)
	if !ok {
		var legacy struct {
			InScope []struct {
				Target string `json:"target"`
			} `json:"in_scope"`
		}
		if json.Unmarshal(data, &legacy) == nil && len(legacy.InScope) > 0 {
			return scopeSummary{Target: legacy.InScope[0].Target}
		}
		return scopeSummary{}
	}
	return scopeSummary{
		Target:    target,
		InCount:   inCount,
		DenyCount: denyCount,
		// Enforcement is unconditional post-unification (#1057): the presence
		// of a machine block with in-scope entries is the enforce state. A
		// legacy document's "mode" key is ignored, matching the runtime.
		Enforced: inCount > 0,
	}
}

// readMachineScopeFull parses the machine_enforcement block, returning the
// usable in-scope entry count, out-of-scope entry count and the first
// in-scope target. ok is false when the block is absent or unusable.
func readMachineScopeFull(data []byte) (inCount, denyCount int, target string, ok bool) {
	var doc struct {
		MachineEnforcement *struct {
			InScope    []json.RawMessage `json:"in_scope"`
			OutOfScope []json.RawMessage `json:"out_of_scope"`
		} `json:"machine_enforcement"`
	}
	if err := json.Unmarshal(data, &doc); err != nil || doc.MachineEnforcement == nil {
		return 0, 0, "", false
	}
	block := doc.MachineEnforcement
	for _, raw := range block.InScope {
		if entry, entryOk := scopeEntryTarget(raw); entryOk {
			inCount++
			if target == "" {
				target = entry
			}
		}
	}
	denyCount = len(block.OutOfScope)
	return inCount, denyCount, target, inCount > 0
}

// readScopeTargets returns the engagement's in/out scope entries as plain
// target strings, used to seed the draft-edit form.
func readScopeTargets(root string) (in, out []string) {
	data, err := os.ReadFile(filepath.Join(root, "plan", "roe.json"))
	if err != nil {
		return nil, nil
	}
	var doc struct {
		MachineEnforcement *struct {
			InScope    []json.RawMessage `json:"in_scope"`
			OutOfScope []json.RawMessage `json:"out_of_scope"`
		} `json:"machine_enforcement"`
	}
	if json.Unmarshal(data, &doc) != nil || doc.MachineEnforcement == nil {
		return nil, nil
	}
	for _, raw := range doc.MachineEnforcement.InScope {
		if t, ok := scopeEntryTarget(raw); ok {
			in = append(in, t)
		}
	}
	for _, raw := range doc.MachineEnforcement.OutOfScope {
		if t, ok := scopeEntryTarget(raw); ok {
			out = append(out, t)
		}
	}
	return in, out
}

// scopeReady reports whether the engagement has an enforced machine scope.
// Creation is the authorization — an engagement created with targets carries
// a "plan/roe.json" machine block and needs no separate approval marker.
func scopeReady(home, slug string) bool {
	summary := readScopeSummary(filepath.Join(home, "workspace", slug))
	return summary.Enforced
}

func scopeEntryTarget(raw json.RawMessage) (string, bool) {
	var target string
	if json.Unmarshal(raw, &target) == nil && target != "" {
		return target, true
	}
	var entry struct {
		Target string `json:"target"`
	}
	if json.Unmarshal(raw, &entry) == nil && entry.Target != "" {
		return entry.Target, true
	}
	return "", false
}

func ScanEngagements(home string) ([]EngagementEntry, error) {
	root := filepath.Join(home, "workspace")
	entries, err := os.ReadDir(root)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("read workspace: %w", err)
	}

	var out []EngagementEntry
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		slug := e.Name()
		if strings.HasPrefix(slug, ".") {
			continue
		}
		scope := readScopeSummary(filepath.Join(root, slug))
		entry := EngagementEntry{Slug: slug, Ready: scope.Enforced, Started: IsStarted(filepath.Join(root, slug)), Scope: scope}
		if entry.Ready {
			if st, err := os.Stat(filepath.Join(root, slug, "plan", "roe.json")); err == nil {
				entry.mtime = st.ModTime().Unix()
			}
		} else if info, err := e.Info(); err == nil {
			entry.mtime = info.ModTime().Unix()
		}
		out = append(out, entry)
	}

	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Ready != out[j].Ready {
			return out[i].Ready
		}
		return out[i].mtime > out[j].mtime
	})
	return out, nil
}

func listAllSlugs(home string) ([]string, error) {
	all, err := ScanEngagements(home)
	if err != nil {
		return nil, err
	}
	out := make([]string, len(all))
	for i, e := range all {
		out[i] = e.Slug
	}
	return out, nil
}

// validateSlug enforces the slug regex and rejects collisions with any
// existing directory under home/workspace.
func validateSlug(home, slug string) error {
	if !slugRe.MatchString(slug) {
		return fmt.Errorf(
			"engagement name must be 3-64 chars, lowercase letters / digits / "+
				"internal hyphens (got %q)",
			slug,
		)
	}
	existing, err := listAllSlugs(home)
	if err != nil {
		return err
	}
	for _, s := range existing {
		if s == slug {
			return fmt.Errorf("engagement %q already exists — pick a different name or resume it", slug)
		}
	}
	return nil
}

// ─── Bubble Tea picker ────────────────────────────────────────────────────

type pickerItem struct {
	slug       string // empty for the "[+] New" sentinel
	isNew      bool
	ready      bool
	started    bool // scope locked (draft when ready && !started)
	inProgress bool
	scope      scopeSummary
}

func (i pickerItem) FilterValue() string {
	if i.isNew {
		return ""
	}
	return i.slug
}

func (i pickerItem) Title() string {
	if i.isNew {
		return "[+] New engagement"
	}
	if i.inProgress {
		return i.slug + "  (in progress)"
	}
	if !i.started {
		return i.slug + "  (draft — scope editable)"
	}
	return i.slug + "  (locked)"
}

func (i pickerItem) Description() string {
	if i.isNew {
		return "Create a new engagement workspace"
	}
	if i.inProgress {
		return "No enforced scope: create with targets or import plan/roe.json"
	}
	state := "draft — edit scope before starting"
	if i.started {
		state = "locked — scope immutable (new engagement for a different scope)"
	}
	return fmt.Sprintf("Red ready · %d in scope · %d denied · %s · %s", i.scope.InCount, i.scope.DenyCount, i.scope.Target, state)
}

type pickerKeyMap struct {
	// Quit reuses the list's built-in `q` / ctrl+c binding — we only intercept
	// the keystroke so we can set a quit result before tea.Quit. We do NOT
	// publish it through AdditionalShortHelpKeys (the default keymap already
	// renders "q quit"); doing so produced a duplicate hint.
	Quit   key.Binding
	Delete key.Binding
}

func newPickerKeys() pickerKeyMap {
	return pickerKeyMap{
		Quit:   key.NewBinding(key.WithKeys("q", "ctrl+c")),
		Delete: key.NewBinding(key.WithKeys("d"), key.WithHelp("d", "delete")),
	}
}

type pickerResult int

const (
	resultPending pickerResult = iota
	resultPicked
	resultQuit
)

type pickerModel struct {
	home string
	list list.Model
	keys pickerKeyMap

	// Last reported terminal size — kept so we can recompute list height
	// whenever the confirm overlay shows or hides.
	width  int
	height int

	// Inline delete confirmation overlay.
	confirmDelete bool
	deleteTarget  string

	// Outcome.
	result   pickerResult
	chosen   pickerItem
	quitting bool
}

// resizeList recomputes the list dimensions for the current window size,
// shrinking by the actual rendered height of the confirm overlay (computed
// at the live terminal width) when it is visible.
func (m *pickerModel) resizeList() {
	if m.width == 0 || m.height == 0 {
		return
	}
	height := m.height - 2
	if m.confirmDelete {
		// Render the overlay at the current width and reserve its exact
		// height + the leading newline that View() inserts before it. This
		// keeps the layout correct even when the body wraps on narrow
		// terminals.
		overlay := m.renderConfirmOverlay()
		height -= lipgloss.Height(overlay) + 1
	}
	if height < 1 {
		height = 1
	}
	m.list.SetSize(m.width, height)
}

// renderConfirmOverlay produces the boxed delete-confirm dialog. Shared
// between resizeList (for height measurement) and View (for actual render).
func (m pickerModel) renderConfirmOverlay() string {
	body := lipgloss.JoinVertical(lipgloss.Left,
		confirmTextStyle.Render(fmt.Sprintf("Permanently delete '%s'?", m.deleteTarget)),
		confirmHintStyle.Render("Removes ~/.decepticon/workspace/"+m.deleteTarget+"/ and all contents."),
		confirmHintStyle.Render("Press [y] to confirm, [n] / [esc] to cancel."),
	)
	return confirmStyle.Render(body)
}

func newPickerModel(home string, entries []EngagementEntry) pickerModel {
	items := buildItems(entries)
	delegate := list.NewDefaultDelegate()
	delegate.Styles.SelectedTitle = delegate.Styles.SelectedTitle.Foreground(lipgloss.Color(ui.ColorFocus)).BorderForeground(lipgloss.Color(ui.ColorFocus)).Background(lipgloss.Color(ui.ColorRaised))
	delegate.Styles.SelectedDesc = delegate.Styles.SelectedDesc.Foreground(lipgloss.Color(ui.ColorText)).BorderForeground(lipgloss.Color(ui.ColorFocus)).Background(lipgloss.Color(ui.ColorRaised))
	delegate.Styles.NormalTitle = delegate.Styles.NormalTitle.Foreground(lipgloss.Color(ui.ColorText))
	delegate.Styles.NormalDesc = delegate.Styles.NormalDesc.Foreground(lipgloss.Color(ui.ColorMuted))
	l := list.New(items, delegate, 0, 0)
	l.Title = fmt.Sprintf("Decepticon · engagements (%d)", len(entries))
	l.SetShowHelp(true)
	l.SetShowStatusBar(false)
	l.SetFilteringEnabled(false)
	l.Styles.Title = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color(ui.ColorText))

	keys := newPickerKeys()
	l.AdditionalShortHelpKeys = func() []key.Binding { return []key.Binding{keys.Delete} }
	l.AdditionalFullHelpKeys = func() []key.Binding { return []key.Binding{keys.Delete} }

	return pickerModel{home: home, list: l, keys: keys}
}

func buildItems(entries []EngagementEntry) []list.Item {
	items := []list.Item{pickerItem{isNew: true}}
	for _, e := range entries {
		items = append(items, pickerItem{
			slug:       e.Slug,
			ready:      e.Ready,
			started:    e.Started,
			inProgress: !e.Ready,
			scope:      e.Scope,
		})
	}
	return items
}

func (m pickerModel) Init() tea.Cmd { return nil }

func (m pickerModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.resizeList()
		return m, nil
	case tea.MouseWheelMsg:
		if !m.confirmDelete && !ui.DisableMouse && os.Getenv("DECEPTICON_MOUSE") != "0" {
			switch msg.Button {
			case tea.MouseWheelUp:
				m.list.Select(max(0, m.list.Index()-1))
			case tea.MouseWheelDown:
				m.list.Select(min(len(m.list.Items())-1, m.list.Index()+1))
			}
		}
		return m, nil
	case tea.MouseClickMsg:
		if !m.confirmDelete && !ui.DisableMouse && os.Getenv("DECEPTICON_MOUSE") != "0" && msg.Button == tea.MouseLeft {
			// Default list delegate: two content rows plus one spacing row.
			// TitleBar adds one bottom-padding row. A click selects only;
			// Enter remains the explicit action that opens an engagement.
			top := lipgloss.Height(m.list.Styles.TitleBar.Render(m.list.Styles.Title.Render(m.list.Title)))
			index := m.list.Paginator.Page*m.list.Paginator.PerPage + (msg.Y-top)/3
			if msg.Y >= top && index >= 0 && index < len(m.list.Items()) && (msg.Y-top)%3 < 2 {
				m.list.Select(index)
			}
		}
		return m, nil

	case tea.KeyMsg:
		if m.confirmDelete {
			switch strings.ToLower(msg.String()) {
			case "y":
				target := filepath.Join(m.home, "workspace", m.deleteTarget)
				_ = os.RemoveAll(target)
				m.confirmDelete = false
				m.deleteTarget = ""
				m.resizeList()
				return m.refresh(), nil
			case "n", "esc":
				m.confirmDelete = false
				m.deleteTarget = ""
				m.resizeList()
				return m, nil
			}
			return m, nil
		}

		switch {
		case key.Matches(msg, m.keys.Quit):
			m.result = resultQuit
			m.quitting = true
			return m, tea.Quit

		case key.Matches(msg, m.keys.Delete):
			if it, ok := m.list.SelectedItem().(pickerItem); ok && !it.isNew {
				m.confirmDelete = true
				m.deleteTarget = it.slug
				m.resizeList()
			}
			return m, nil
		}

		if msg.String() == "enter" {
			if it, ok := m.list.SelectedItem().(pickerItem); ok {
				m.chosen = it
				m.result = resultPicked
				m.quitting = true
				return m, tea.Quit
			}
		}
	}

	var cmd tea.Cmd
	m.list, cmd = m.list.Update(msg)
	return m, cmd
}

func (m pickerModel) refresh() pickerModel {
	all, _ := ScanEngagements(m.home)
	m.list.SetItems(buildItems(all))
	return m
}

var (
	confirmStyle = lipgloss.NewStyle().
			Padding(0, 1).
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color(ui.ColorText))
	confirmTextStyle = ui.Bold
	confirmHintStyle = ui.Dim
)

func (m pickerModel) View() tea.View {
	if m.quitting {
		return tea.NewView("")
	}
	content := m.list.View()
	if m.confirmDelete {
		content += "\n" + m.renderConfirmOverlay()
	}
	v := tea.NewView(content)
	v.AltScreen = true
	if !ui.DisableMouse && os.Getenv("DECEPTICON_MOUSE") != "0" {
		v.MouseMode = tea.MouseModeCellMotion
	}
	return v
}

// Select runs the engagement picker.
func Select(home string) (Choice, error) {
	all, err := ScanEngagements(home)
	if err != nil {
		ui.Warning("Could not scan engagements: " + err.Error())
		all = nil
	}

	model := newPickerModel(home, all)
	finalModel, err := tea.NewProgram(model).Run()
	if err != nil {
		return Choice{}, fmt.Errorf("engagement picker failed: %w", err)
	}
	final, ok := finalModel.(pickerModel)
	if !ok {
		return Choice{}, fmt.Errorf("engagement picker: unexpected model type")
	}
	if final.result != resultPicked {
		return Choice{}, fmt.Errorf("engagement picker cancelled")
	}

	if final.chosen.isNew {
		slug, lists, opts, err := promptNewEngagement(home)
		if err != nil {
			return Choice{}, err
		}
		return createDirectEngagement(home, slug, lists, opts, time.Now())
	}

	slug := final.chosen.slug
	// A draft (scope present, not yet started) may still be edited; starting
	// the engagement locks it. Offer the edit before handing off.
	if scopeReady(home, slug) && !IsStarted(filepath.Join(home, "workspace", slug)) {
		var edit bool
		confirm := huh.NewForm(
			huh.NewGroup(
				huh.NewConfirm().
					Title("\""+slug+"\" is a draft — edit its scope before starting?").
					Description("Starting the engagement locks the scope (a different scope then needs a new engagement). Edit to adjust in/out scope with passive suggestions, or Keep to start as-is.").
					Affirmative("Edit").
					Negative("Keep").
					Value(&edit),
			),
		).WithTheme(huh.ThemeFunc(ui.DecepticonTheme))
		// On non-interactive stdin the confirm errors → keep as-is (safe).
		if err := confirm.Run(); err == nil && edit {
			lists, opts, err := promptEditDraftScope(home, slug)
			if err != nil {
				return Choice{}, err
			}
			if err := writeDraftScope(home, slug, lists, opts, time.Now()); err != nil {
				return Choice{}, err
			}
		}
	}

	return choiceForEngagement(home, slug), nil
}

func ResolveExisting(home, slug string) (Choice, error) {
	return ResolveExistingAt(home, slug, "")
}

func ResolveExistingAt(home, slug, documentRoot string) (Choice, error) {
	if !slugRe.MatchString(slug) {
		return Choice{}, fmt.Errorf("invalid engagement name %q", slug)
	}
	dir := filepath.Join(home, "workspace", slug)
	if documentRoot != "" {
		absoluteHome, err := filepath.Abs(home)
		if err != nil {
			return Choice{}, err
		}
		absoluteRoot, err := filepath.Abs(documentRoot)
		if err != nil {
			return Choice{}, err
		}
		relative, err := filepath.Rel(absoluteHome, absoluteRoot)
		if err != nil {
			return Choice{}, err
		}
		parts := strings.Split(relative, string(filepath.Separator))
		if len(parts) != 2 || parts[0] == ".." || parts[0] == "." || parts[1] == ".." || parts[1] == "." {
			return Choice{}, fmt.Errorf("desktop engagement documents must belong to this Local home")
		}
		for _, parent := range []string{absoluteHome, filepath.Join(absoluteHome, parts[0])} {
			info, err := os.Lstat(parent)
			if err != nil {
				return Choice{}, err
			}
			if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
				return Choice{}, fmt.Errorf("desktop document parent is not a regular directory")
			}
		}
		dir = absoluteRoot
	}
	info, err := os.Lstat(dir)
	if err != nil {
		return Choice{}, fmt.Errorf("inspect engagement: %w", err)
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return Choice{}, fmt.Errorf("engagement %q is not a regular directory", slug)
	}
	summary := readScopeSummary(dir)
	return Choice{AssistantID: AssistantDecepticon, Engagement: slug, WorkspacePath: dir, Mode: "red", Target: summary.Target, AuthorizationConfirmed: summary.Target != ""}, nil
}

func choiceForEngagement(home, slug string) Choice {
	workspace := filepath.Join(home, "workspace", slug)
	summary := readScopeSummary(workspace)
	return Choice{
		AssistantID:            AssistantDecepticon,
		Engagement:             slug,
		WorkspacePath:          workspace,
		Mode:                   "red",
		Target:                 summary.Target,
		AuthorizationConfirmed: summary.Target != "",
	}
}

// promptNewEngagement collects the creation inputs, compiles them through the
// shared normalization contract, and shows the generated scope for explicit
// confirmation before the engagement is created — the review sees exactly
// what the authorization will enforce.
// promptScopeSuggestions shows the discovery recommendations as a
// pre-checked multi-select (unchecking removes an entry from the scope)
// plus a free-text field for hosts the sources missed. Cancelling the form
// skips the suggestions entirely — the operator's typed targets remain the
// baseline either way.
func promptScopeSuggestions(recommendations []string) (accepted []string, additional string, err error) {
	if len(recommendations) == 0 {
		return nil, "", nil
	}
	selected := append([]string(nil), recommendations...)
	options := make([]huh.Option[string], 0, len(recommendations))
	for _, recommendation := range recommendations {
		options = append(options, huh.NewOption(recommendation, recommendation).Selected(true))
	}
	height := len(options)
	if height > 12 {
		height = 12
	}
	form := huh.NewForm(
		huh.NewGroup(
			huh.NewMultiSelect[string]().
				Title("Recommended in-scope entries").
				Description("Passive discovery (CT logs + passive DNS) found these hosts in the target's zone.\nSPACE toggles, ENTER confirms — unchecked entries stay out of scope.\nYour typed targets remain in scope regardless.").
				Filterable(true).
				Height(height).
				Options(options...).
				Value(&selected),
			huh.NewInput().
				Title("Additional in-scope (optional)").
				Description("Anything the discovery missed — same format as the target scope.").
				Placeholder("admin.decepticon.red, 10.0.0.0/24").
				Value(&additional),
		).Title("Scope suggestions"),
	).WithTheme(huh.ThemeFunc(ui.DecepticonTheme))
	if err := form.Run(); err != nil {
		return nil, "", fmt.Errorf("scope suggestions cancelled: %w", err)
	}
	if selected == nil {
		selected = []string{}
	}
	return selected, additional, nil
}

func promptNewEngagement(home string) (string, scopeLists, scopeOptions, error) {
	var slug, targets, outOfScope string
	form := huh.NewForm(
		huh.NewGroup(
			huh.NewInput().
				Title("Engagement name").
				Description("Lowercase, hyphens allowed. Used as the workspace directory name (e.g., acme-external-2026).").
				Placeholder("e.g., acme-external-2026").
				Value(&slug).
				Validate(func(s string) error { return validateSlug(home, s) }),
			huh.NewInput().Title("Target scope").
				Description("Targets you are authorized to test — URLs, hostnames, CIDRs, or *.domain globs; commas or spaces separate entries. Creating the engagement with a scope is the authorization.").
				Placeholder("example.com, 10.0.0.0/24, *.decepticon.red").
				Value(&targets).
				Validate(func(s string) error {
					in, _, err := dedupeScopeTargets(splitScopeTargets([]string{s}))
					if err != nil {
						return err
					}
					if len(in) == 0 {
						return fmt.Errorf("enter at least one in-scope target")
					}
					return nil
				}),
			huh.NewInput().Title("Out of scope (optional)").
				Description("Entries the engagement must never touch; they override any in-scope match.").
				Placeholder("admin.example.com, 10.0.0.5/32").
				Value(&outOfScope).
				Validate(func(s string) error {
					if strings.TrimSpace(s) == "" {
						return nil
					}
					_, _, err := dedupeScopeTargets(splitScopeTargets([]string{s}))
					return err
				}),
		).Title("New engagement"),
	).WithTheme(huh.ThemeFunc(ui.DecepticonTheme))
	if err := form.Run(); err != nil {
		return "", scopeLists{}, scopeOptions{}, fmt.Errorf("engagement setup cancelled: %w", err)
	}
	if err := validateSlug(home, slug); err != nil {
		return "", scopeLists{}, scopeOptions{}, err
	}
	lists, opts, err := collectScope(slug, targets, outOfScope, "Create", scopeOptions{})
	if err != nil {
		return "", scopeLists{}, scopeOptions{}, err
	}
	return slug, lists, opts, nil
}

// collectScope runs passive discovery + suggestions on the typed targets,
// normalizes them to the enforced scope lists, collects the optional enforced
// RoE controls (seeded for edit), and shows a preview/confirm gate. Shared by
// create and draft-edit; verb is "Create" or "Save".
func collectScope(slug, targets, outOfScope, verb string, seed scopeOptions) (scopeLists, scopeOptions, error) {
	// The form's validators do not fire when stdin ends mid-form; re-verify the
	// contract here so an EOF can never yield a scopeless engagement.
	in, _, err := dedupeScopeTargets(splitScopeTargets([]string{targets}))
	if err != nil {
		return scopeLists{}, scopeOptions{}, err
	}
	if len(in) == 0 {
		return scopeLists{}, scopeOptions{}, fmt.Errorf("scope entry cancelled: no in-scope target entered")
	}
	if _, _, err := dedupeScopeTargets(splitScopeTargets([]string{outOfScope})); err != nil {
		return scopeLists{}, scopeOptions{}, err
	}

	// Passive discovery: CT logs + passive DNS suggest in-scope entries for
	// the first hostname target. Strictly optional — the network never
	// blocks scope entry; a timeout or degradation folds back to the typed
	// targets (the operator always owns what goes in scope).
	if zone := discoveryZone(in); zone != "" {
		ui.Info(fmt.Sprintf("Checking passive scope suggestions (up to %s)...", scopeDiscoveryBudget))
		discovery := DiscoverScope(zone, scopeDiscoveryBudget)
		switch {
		case len(discovery.Recommended) > 0 && len(discovery.Notes) > 0:
			ui.Info(fmt.Sprintf("Some passive sources did not respond: %s. Review the available suggestions or enter scope manually.", strings.Join(discovery.Notes, "; ")))
		case len(discovery.Recommended) == 0:
			ui.Info(fmt.Sprintf("Passive discovery found nothing: %s. Continuing with your typed targets.", strings.Join(discovery.Notes, "; ")))
		default:
			ui.Info(fmt.Sprintf("Passive discovery: %d suggestion(s) in the %s zone.", len(discovery.Recommended), zone))
		}
		if accepted, additional, suggestErr := promptScopeSuggestions(discovery.Recommended); suggestErr == nil {
			// A cancelled suggestion form counts as "skip suggestions" and
			// keeps the typed targets — only accepted entries merge in.
			if len(accepted) > 0 {
				targets = strings.TrimSpace(targets + " " + strings.Join(accepted, " "))
			}
			if extra := strings.TrimSpace(additional); extra != "" {
				targets = strings.TrimSpace(targets + " " + extra)
			}
			if in, _, err = dedupeScopeTargets(splitScopeTargets([]string{targets})); err != nil {
				return scopeLists{}, scopeOptions{}, err
			}
		}
	}

	lists, err := buildScopeLists([]string{targets}, []string{outOfScope})
	if err != nil {
		return scopeLists{}, scopeOptions{}, err
	}

	// Optional enforced controls (seeded for edit). Only fields the runtime
	// actually enforces are offered.
	opts, err := promptScopeOptions(seed)
	if err != nil {
		return scopeLists{}, scopeOptions{}, err
	}

	title := "Create this engagement?"
	if verb == "Save" {
		title = "Save scope changes?"
	}
	var confirmed bool
	preview := huh.NewForm(
		huh.NewGroup(
			huh.NewNote().
				Title("Review the generated scope").
				Description(renderScopePreview(slug, lists, opts)),
			huh.NewConfirm().
				Title(title).
				Affirmative(verb).
				Negative("Cancel").
				Value(&confirmed),
		),
	).WithTheme(huh.ThemeFunc(ui.DecepticonTheme))
	if err := preview.Run(); err != nil {
		return scopeLists{}, scopeOptions{}, fmt.Errorf("scope review cancelled: %w", err)
	}
	if !confirmed {
		return scopeLists{}, scopeOptions{}, fmt.Errorf("scope review rejected")
	}
	return lists, opts, nil
}

// promptScopeOptions collects the optional enforced RoE controls: the authorized
// window length, extra forbidden destinations, and the cloud-metadata /
// sensitive-TLD allow toggles. Seeded with the current values on edit. A
// cancelled/EOF form keeps the seed (safe defaults on create).
func promptScopeOptions(seed scopeOptions) (scopeOptions, error) {
	windowStr := strconv.Itoa(seed.windowDays())
	forbidden := strings.Join(seed.ForbiddenDestinations, ", ")
	allowCloud := seed.AllowCloudMetadata
	allowTLD := seed.AllowSensitiveTLDs
	form := huh.NewForm(
		huh.NewGroup(
			huh.NewInput().Title("Authorized window (days)").
				Description("How long the engagement is authorized to run from now. Outside this window the agent pauses.").
				Value(&windowStr).
				Validate(func(s string) error {
					n, err := strconv.Atoi(strings.TrimSpace(s))
					if err != nil || n <= 0 {
						return fmt.Errorf("enter a positive number of days")
					}
					return nil
				}),
			huh.NewInput().Title("Forbidden destinations (optional)").
				Description("Always-denied hosts/IPs, in addition to the out-of-scope list and the built-in cloud-metadata guard.").
				Value(&forbidden).
				Validate(func(s string) error {
					if strings.TrimSpace(s) == "" {
						return nil
					}
					_, _, err := dedupeScopeTargets(splitScopeTargets([]string{s}))
					return err
				}),
			huh.NewConfirm().Title("Allow cloud-metadata endpoints?").
				Description("169.254.169.254 and friends. Default: blocked (recommended).").
				Affirmative("Allow").Negative("Block").Value(&allowCloud),
			huh.NewConfirm().Title("Allow sensitive TLDs (.gov/.mil/.edu/.int)?").
				Description("Default: blocked (recommended).").
				Affirmative("Allow").Negative("Block").Value(&allowTLD),
		).Title("Scope options (optional)"),
	).WithTheme(huh.ThemeFunc(ui.DecepticonTheme))
	if err := form.Run(); err != nil {
		// EOF/cancel → keep the seed (defaults on create).
		return seed, nil
	}
	days, err := strconv.Atoi(strings.TrimSpace(windowStr))
	if err != nil || days <= 0 {
		days = seed.windowDays()
	}
	forb, _, _ := dedupeScopeTargets(splitScopeTargets([]string{forbidden}))
	return scopeOptions{
		WindowDays:            days,
		ForbiddenDestinations: forb,
		AllowCloudMetadata:    allowCloud,
		AllowSensitiveTLDs:    allowTLD,
	}, nil
}

// promptEditDraftScope re-collects the scope for a draft engagement, seeding the
// form with its current in/out targets. Only valid while the engagement is a
// draft (the caller gates on !IsStarted); the writer refuses once started.
func promptEditDraftScope(home, slug string) (scopeLists, scopeOptions, error) {
	root := filepath.Join(home, "workspace", slug)
	curIn, curOut := readScopeTargets(root)
	seed := readScopeOptions(root)
	targets := strings.Join(curIn, " ")
	outOfScope := strings.Join(curOut, " ")
	form := huh.NewForm(
		huh.NewGroup(
			huh.NewInput().Title("Target scope").
				Description("In-scope targets — URLs, hostnames, CIDRs, or *.domain globs; commas or spaces separate entries.").
				Value(&targets).
				Validate(func(s string) error {
					in, _, err := dedupeScopeTargets(splitScopeTargets([]string{s}))
					if err != nil {
						return err
					}
					if len(in) == 0 {
						return fmt.Errorf("enter at least one in-scope target")
					}
					return nil
				}),
			huh.NewInput().Title("Out of scope (optional)").
				Description("Entries the engagement must never touch; they override any in-scope match.").
				Value(&outOfScope).
				Validate(func(s string) error {
					if strings.TrimSpace(s) == "" {
						return nil
					}
					_, _, err := dedupeScopeTargets(splitScopeTargets([]string{s}))
					return err
				}),
		).Title("Edit draft scope — "+slug),
	).WithTheme(huh.ThemeFunc(ui.DecepticonTheme))
	if err := form.Run(); err != nil {
		return scopeLists{}, scopeOptions{}, fmt.Errorf("scope edit cancelled: %w", err)
	}
	return collectScope(slug, targets, outOfScope, "Save", seed)
}

// renderScopePreview renders the normalized scope + options the engagement will
// enforce.
func renderScopePreview(slug string, lists scopeLists, opts scopeOptions) string {
	// Huh notes interpret *, _ and backticks as formatting. Escape scope
	// patterns so the operator reviews the exact authorized targets.
	escape := strings.NewReplacer("\\", "\\\\", "*", "\\*", "_", "\\_", "`", "\\`")
	join := func(targets []string) string { return escape.Replace(strings.Join(targets, ", ")) }
	var b strings.Builder
	fmt.Fprintf(&b, "Engagement %q scope — this scope is the authorization.\n\n", slug)
	fmt.Fprintf(&b, "In scope (%d): %s\n", len(lists.InScope), join(lists.InScope))
	if len(lists.OutOfScope) > 0 {
		fmt.Fprintf(&b, "Out of scope: %s\n", join(lists.OutOfScope))
	}
	if len(lists.Dropped) > 0 {
		fmt.Fprintf(&b, "Dropped (%d): %s\n", len(lists.Dropped), join(lists.Dropped))
		fmt.Fprintf(&b, "  (no DNS/IP target can match a dropped entry)\n")
	}
	fmt.Fprintf(&b, "Authorized window: %d day(s) from now\n", opts.windowDays())
	if len(opts.ForbiddenDestinations) > 0 {
		fmt.Fprintf(&b, "Forbidden destinations: %s\n", join(opts.ForbiddenDestinations))
	}
	fmt.Fprintf(&b, "Cloud metadata: %s · Sensitive TLDs: %s\n", allowLabel(opts.AllowCloudMetadata), allowLabel(opts.AllowSensitiveTLDs))
	b.WriteString("\nRed enforces this scope on every tool call.")
	return b.String()
}

func allowLabel(allow bool) string {
	if allow {
		return "allowed"
	}
	return "blocked"
}

// readScopeOptions reads the optional enforced controls from an engagement's
// roe.json to seed the draft-edit form. Missing fields fall back to defaults.
func readScopeOptions(root string) scopeOptions {
	data, err := os.ReadFile(filepath.Join(root, "plan", "roe.json"))
	if err != nil {
		return scopeOptions{}
	}
	var doc struct {
		MachineEnforcement *struct {
			AuthorizedWindows []struct {
				Start string `json:"start"`
				End   string `json:"end"`
			} `json:"authorized_windows"`
			ForbiddenDestinations []string `json:"forbidden_destinations"`
			AllowCloudMetadata    bool     `json:"allow_cloud_metadata"`
			AllowSensitiveTLDs    bool     `json:"allow_sensitive_tlds"`
		} `json:"machine_enforcement"`
	}
	if json.Unmarshal(data, &doc) != nil || doc.MachineEnforcement == nil {
		return scopeOptions{}
	}
	m := doc.MachineEnforcement
	opts := scopeOptions{
		ForbiddenDestinations: m.ForbiddenDestinations,
		AllowCloudMetadata:    m.AllowCloudMetadata,
		AllowSensitiveTLDs:    m.AllowSensitiveTLDs,
	}
	if len(m.AuthorizedWindows) > 0 {
		if start, err1 := time.Parse(time.RFC3339, m.AuthorizedWindows[0].Start); err1 == nil {
			if end, err2 := time.Parse(time.RFC3339, m.AuthorizedWindows[0].End); err2 == nil {
				if days := int(end.Sub(start).Hours() / 24); days > 0 {
					opts.WindowDays = days
				}
			}
		}
	}
	return opts
}
