// Package engagement scans the host workspace for engagements and runs the
// launcher-side picker. The picker is split between two libraries:
//
//   - The main list (engagement selection + inline delete) is a custom
//     bubbletea + bubbles/list program because per-item action keys ('d'
//     for delete) are not in huh's form vocabulary.
//   - The slug-input prompt that follows "[+] New" stays on huh — it is a
//     single-field form with regex validation, exactly what huh is for.
package engagement

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"charm.land/bubbles/v2/key"
	"charm.land/bubbles/v2/list"
	tea "charm.land/bubbletea/v2"
	"charm.land/huh/v2"
	"charm.land/lipgloss/v2"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/ui"
)

// AssistantSoundwave drives the document-writing interview for a fresh engagement.
const AssistantSoundwave = "soundwave"

// AssistantDecepticon drives kill-chain execution against an existing engagement.
const AssistantDecepticon = "decepticon"

var planningDocuments = []string{
	"roe.json", "threat-profile.json", "conops.json", "deconfliction.json",
	"contact.json", "data-handling.json", "abort.json", "cleanup.json",
}

// Slug regex: lowercase alphanumeric with internal hyphens, 3-64 chars.
var slugRe = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$`)

// Choice carries the picker result back to the launcher.
type Choice struct {
	AssistantID   string
	Engagement    string
	WorkspacePath string
}

// engagementEntry pairs a slug with metadata used for picker rendering and
// downstream assistant selection.
type engagementEntry struct {
	Slug  string
	Ready bool
	mtime int64
}

// isReady reports whether a single engagement carries the full planning
// bundle (roe.json + conops.json + deconfliction.json).
func isReady(home, slug string) bool {
	plan := filepath.Join(home, "workspace", slug, "plan")
	for _, name := range []string{"roe.json", "conops.json", "deconfliction.json"} {
		if _, err := os.Stat(filepath.Join(plan, name)); err != nil {
			return false
		}
	}
	return true
}

func isRedApproved(home, slug string) bool {
	root := filepath.Join(home, "workspace", slug)
	marker, err := os.ReadFile(filepath.Join(root, ".red-approved"))
	if err != nil {
		return false
	}
	digest := sha256.New()
	for _, filename := range planningDocuments {
		content, err := os.ReadFile(filepath.Join(root, "plan", filename))
		if err != nil {
			return false
		}
		_, _ = digest.Write([]byte(filename))
		_, _ = digest.Write([]byte{0})
		_, _ = digest.Write(content)
		_, _ = digest.Write([]byte{0})
	}
	return strings.TrimSpace(string(marker)) == hex.EncodeToString(digest.Sum(nil))
}

// ScanEngagements returns every directory under home/workspace/ regardless
// of completeness. Sort: ready engagements first (most recent RoE), then
// in-progress engagements (most recent dir mtime).
func ScanEngagements(home string) ([]engagementEntry, error) {
	root := filepath.Join(home, "workspace")
	entries, err := os.ReadDir(root)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("read workspace: %w", err)
	}

	var out []engagementEntry
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		slug := e.Name()
		if strings.HasPrefix(slug, ".") {
			continue
		}
		entry := engagementEntry{Slug: slug, Ready: isReady(home, slug)}
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
	inProgress bool
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
	return i.slug
}

func (i pickerItem) Description() string {
	if i.isNew {
		return "Create a new engagement workspace"
	}
	if i.inProgress {
		return "Planning incomplete — resume with Soundwave"
	}
	return "Resume with Decepticon"
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

func newPickerModel(home string, entries []engagementEntry) pickerModel {
	items := buildItems(entries)
	delegate := list.NewDefaultDelegate()
	l := list.New(items, delegate, 0, 0)
	l.Title = "Decepticon — pick an engagement"
	l.SetShowHelp(true)
	l.SetShowStatusBar(false)
	l.SetFilteringEnabled(false)
	l.Styles.Title = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("#ef4444"))

	keys := newPickerKeys()
	l.AdditionalShortHelpKeys = func() []key.Binding { return []key.Binding{keys.Delete} }
	l.AdditionalFullHelpKeys = func() []key.Binding { return []key.Binding{keys.Delete} }

	return pickerModel{home: home, list: l, keys: keys}
}

func buildItems(entries []engagementEntry) []list.Item {
	items := []list.Item{pickerItem{isNew: true}}
	for _, e := range entries {
		items = append(items, pickerItem{
			slug:       e.Slug,
			ready:      e.Ready,
			inProgress: !e.Ready,
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
			BorderForeground(lipgloss.Color("#ef4444"))
	confirmTextStyle = lipgloss.NewStyle().Bold(true)
	confirmHintStyle = lipgloss.NewStyle().Faint(true)
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

	root := filepath.Join(home, "workspace")
	if final.chosen.isNew {
		slug, err := promptNewSlug(home)
		if err != nil {
			return Choice{}, err
		}
		return CreateNamed(home, slug)
	}

	slug := final.chosen.slug
	assistant := AssistantSoundwave
	if isRedApproved(home, slug) {
		assistant = AssistantDecepticon
	}
	return Choice{
		AssistantID:   assistant,
		Engagement:    slug,
		WorkspacePath: filepath.Join(root, slug),
	}, nil
}

func CreateNamed(home, slug string) (Choice, error) {
	if err := validateSlug(home, slug); err != nil {
		return Choice{}, err
	}
	root := filepath.Join(home, "workspace")
	if err := os.MkdirAll(root, 0o755); err != nil {
		return Choice{}, fmt.Errorf("create workspace root: %w", err)
	}
	path := filepath.Join(root, slug)
	if err := os.Mkdir(path, 0o755); err != nil {
		return Choice{}, fmt.Errorf("create engagement directory: %w", err)
	}
	if err := os.Mkdir(filepath.Join(path, "plan"), 0o755); err != nil {
		return Choice{}, fmt.Errorf("create engagement plan directory: %w", err)
	}
	return Choice{AssistantID: AssistantSoundwave, Engagement: slug, WorkspacePath: path}, nil
}

// SelectExisting resolves a named workspace without opening the terminal picker.
func SelectExisting(home, slug string) (Choice, error) {
	if !slugRe.MatchString(slug) {
		return Choice{}, fmt.Errorf("invalid engagement name %q", slug)
	}
	path := filepath.Join(home, "workspace", slug)
	info, err := os.Lstat(path)
	if err != nil {
		return Choice{}, fmt.Errorf("engagement %q does not exist: %w", slug, err)
	}
	if !info.IsDir() {
		return Choice{}, fmt.Errorf("engagement %q is not a workspace directory", slug)
	}
	assistant := AssistantSoundwave
	if isRedApproved(home, slug) {
		assistant = AssistantDecepticon
	}
	return Choice{AssistantID: assistant, Engagement: slug, WorkspacePath: path}, nil
}

// promptNewSlug runs a one-shot huh form for the slug-input phase.
func promptNewSlug(home string) (string, error) {
	var slug string
	form := huh.NewForm(
		huh.NewGroup(
			huh.NewInput().
				Title("Engagement name").
				Description("Lowercase, hyphens allowed. Used as the workspace directory name (e.g., acme-external-2026).").
				Placeholder("e.g., acme-external-2026").
				Value(&slug).
				Validate(func(s string) error { return validateSlug(home, s) }),
		).Title("New engagement").Description("Create the engagement workspace"),
	).WithTheme(huh.ThemeFunc(ui.DecepticonTheme))
	if err := form.Run(); err != nil {
		return "", fmt.Errorf("slug input cancelled: %w", err)
	}
	return slug, nil
}
