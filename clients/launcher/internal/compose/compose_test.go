package compose

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/runtime"
)

func TestMCPActionHelperProcess(t *testing.T) {
	if os.Getenv("DECEPTICON_MCP_ACTION_HELPER") != "1" {
		return
	}
	args := strings.Join(os.Args, " ")
	if !strings.Contains(args, "--profile cli run -T --rm --no-deps --entrypoint node cli dist/mcp-action.js blue verify") {
		fmt.Fprintln(os.Stderr, args)
		os.Exit(2)
	}
	fmt.Fprint(os.Stdout, "coverage verified")
	os.Exit(0)
}

func TestMCPActionUsesHeadlessCLIImage(t *testing.T) {
	t.Setenv("DECEPTICON_MCP_ACTION_HELPER", "1")
	t.Setenv("DECEPTICON_STACK_NAME", "")
	stack := &Compose{
		Home: "/test", ComposeFile: "/test/docker-compose.yml", EnvFile: "/test/.env",
		Runtime: runtime.Runtime{Bin: os.Args[0], ComposeArgs: []string{"-test.run=TestMCPActionHelperProcess", "--"}},
	}
	output, err := stack.MCPAction(context.Background(), "blue", "verify")
	if err != nil || output != "coverage verified" {
		t.Fatalf("headless CLI action = %q, %v", output, err)
	}
}

func TestMCPHelperProcess(t *testing.T) {
	if os.Getenv("DECEPTICON_MCP_TEST_HELPER") != "1" {
		return
	}
	separator := -1
	for index, arg := range os.Args {
		if arg == "--" {
			separator = index
			break
		}
	}
	if separator < 0 {
		os.Exit(2)
	}
	args := os.Args[separator+1:]
	want := []string{
		"-p", "decepticon", "-f", "/test/docker-compose.yml",
		"--env-file", "/test/.env", "exec", "-T", "-e",
		"DECEPTICON_SKIP_BOOT=1", "langgraph", "decepticon-mcp",
		"--transport", "stdio",
	}
	if !reflect.DeepEqual(args, want) {
		fmt.Fprintln(os.Stderr, "wrong MCP command:", args)
		os.Exit(3)
	}
	line, err := bufio.NewReader(os.Stdin).ReadString('\n')
	if err != nil {
		os.Exit(4)
	}
	fmt.Fprint(os.Stdout, line)
	os.Exit(0)
}

func TestMCPCommandForwardsJSONRPCWithoutTerminal(t *testing.T) {
	t.Setenv("DECEPTICON_MCP_TEST_HELPER", "1")
	t.Setenv("DECEPTICON_STACK_NAME", "")
	inputReader, inputWriter, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer inputReader.Close()
	defer inputWriter.Close()
	outputReader, outputWriter, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer outputReader.Close()
	defer outputWriter.Close()
	oldInput, oldOutput := os.Stdin, os.Stdout
	os.Stdin, os.Stdout = inputReader, outputWriter
	defer func() { os.Stdin, os.Stdout = oldInput, oldOutput }()

	request := `{"jsonrpc":"2.0","id":1,"method":"initialize"}` + "\n"
	if _, err := io.WriteString(inputWriter, request); err != nil {
		t.Fatal(err)
	}
	if err := inputWriter.Close(); err != nil {
		t.Fatal(err)
	}
	c := &Compose{
		Home:        "/test",
		ComposeFile: "/test/docker-compose.yml",
		EnvFile:     "/test/.env",
		Runtime: runtime.Runtime{
			Name:        "test",
			Bin:         os.Args[0],
			ComposeArgs: []string{"-test.run=TestMCPHelperProcess", "--"},
		},
	}
	command := c.MCPCommand()
	command.Stdin = os.Stdin
	command.Stdout = os.Stdout
	if err := command.Run(); err != nil {
		t.Fatal(err)
	}
	if err := outputWriter.Close(); err != nil {
		t.Fatal(err)
	}
	response, err := io.ReadAll(outputReader)
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(string(response)) != strings.TrimSpace(request) {
		t.Fatalf("stdio response = %q, want %q", response, request)
	}
}

func TestNew(t *testing.T) {
	// filepath.Join is OS-aware (LF separators on Unix, backslash on
	// Windows). Use t.TempDir() to get a valid path on whatever runner
	// executes the test rather than hardcoding a /tmp path that doesn't
	// exist on Windows.
	home := t.TempDir()
	t.Setenv("DECEPTICON_HOME", home)
	c := New()
	if c.Home != home {
		t.Errorf("Home = %q, want %q", c.Home, home)
	}
	if want := filepath.Join(home, "docker-compose.yml"); c.ComposeFile != want {
		t.Errorf("ComposeFile = %q, want %q", c.ComposeFile, want)
	}
	if want := filepath.Join(home, ".env"); c.EnvFile != want {
		t.Errorf("EnvFile = %q, want %q", c.EnvFile, want)
	}
}

func TestAllProfiles(t *testing.T) {
	profiles := AllProfiles()
	// ADR-0006 Sprint 2 expanded the catalog to cover every workload
	// the launcher may need to tear down: cli + c2-sliver + ad +
	// reversing = 4 profiles = 8 cli args (--profile NAME).
	expected := []string{
		"--profile", "cli",
		"--profile", "c2-sliver",
		"--profile", "ad",
		"--profile", "reversing",
	}
	if len(profiles) != len(expected) {
		t.Fatalf("AllProfiles() len = %d, want %d", len(profiles), len(expected))
	}
	for i, v := range expected {
		if profiles[i] != v {
			t.Errorf("profiles[%d] = %q, want %q", i, profiles[i], v)
		}
	}
}

func TestBaseArgs(t *testing.T) {
	t.Setenv("DECEPTICON_STACK_NAME", "")
	c := &Compose{
		Home:        "/test",
		ComposeFile: "/test/docker-compose.yml",
		EnvFile:     "/test/.env",
	}
	args := c.baseArgs()
	// Expected shape: ["compose", "-p", "decepticon", "-f",
	//                  "/test/docker-compose.yml", "--env-file", "/test/.env"]
	// `-p decepticon` is explicit so the launcher and the opscontrol
	// daemon both target the same compose project; otherwise the
	// daemon's no-`-p` default ("decepticon" via dir basename) drifts
	// the moment any caller passes `-p X` themselves.
	want := []string{
		"compose",
		"-p", "decepticon",
		"-f", "/test/docker-compose.yml",
		"--env-file", "/test/.env",
	}
	if len(args) != len(want) {
		t.Fatalf("baseArgs len = %d (%v); want %d (%v)", len(args), args, len(want), want)
	}
	for i, v := range want {
		if args[i] != v {
			t.Errorf("args[%d] = %q, want %q", i, args[i], v)
		}
	}
}

func TestBaseArgs_StackNameOverridesProjectName(t *testing.T) {
	t.Setenv("DECEPTICON_STACK_NAME", "stack2")
	c := &Compose{
		Home:        "/test",
		ComposeFile: "/test/docker-compose.yml",
		EnvFile:     "/test/.env",
	}
	args := c.baseArgs()
	if args[2] != "decepticon-stack2" {
		t.Errorf("expected -p decepticon-stack2 for DECEPTICON_STACK_NAME=stack2; got args=%v", args)
	}
}

func TestImageTag(t *testing.T) {
	tests := map[string]string{
		"v1.0.21":  "1.0.21",
		"1.0.21":   "1.0.21",
		" latest ": "latest",
	}
	for input, want := range tests {
		if got := imageTag(input); got != want {
			t.Errorf("imageTag(%q) = %q, want %q", input, got, want)
		}
	}
}
