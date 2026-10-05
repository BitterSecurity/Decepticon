package mcpbridge

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestMain(m *testing.M) {
	if os.Getenv("DECEPTICON_TEST_LAUNCHER") == "1" && len(os.Args) > 1 {
		if os.Args[1] == "status" {
			fmt.Fprint(os.Stdout, "mock compose ps")
			os.Exit(0)
		}
		fmt.Fprint(os.Stderr, "unsupported command")
		os.Exit(2)
	}
	os.Exit(m.Run())
}

func TestLocalCommandCapturesLauncherOutputAndFailure(t *testing.T) {
	t.Setenv("DECEPTICON_TEST_LAUNCHER", "1")
	output, err := LocalCommand(context.Background(), "status")
	if err != nil || output != "mock compose ps" {
		t.Fatalf("status output = %q, error = %v", output, err)
	}
	if _, err := LocalCommand(context.Background(), "kg-health"); err == nil {
		t.Fatal("expected command failure to propagate")
	}
}

func TestRuntimeToolsAndCLIStatusShareOneServer(t *testing.T) {
	ctx := context.Background()
	t.Setenv("DECEPTICON_HOME", t.TempDir())
	child := mcp.NewServer(&mcp.Implementation{Name: "runtime", Version: "test"}, nil)
	type echoInput struct {
		Text string `json:"text"`
	}
	type echoOutput struct {
		Text string `json:"text"`
	}
	mcp.AddTool(child, &mcp.Tool{Name: "decepticon_echo", Description: "Echo runtime input"},
		func(_ context.Context, _ *mcp.CallToolRequest, input echoInput) (*mcp.CallToolResult, echoOutput, error) {
			return nil, echoOutput{Text: input.Text}, nil
		})
	mcp.AddTool(child, &mcp.Tool{Name: "decepticon_fail", Description: "Return a runtime error"},
		func(_ context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
			return nil, nil, errors.New("runtime unavailable")
		})
	child.AddTool(&mcp.Tool{
		Name: "decepticon_cursor", Description: "Preserve a large event cursor",
		InputSchema: map[string]any{"type": "object", "properties": map[string]any{
			"after": map[string]any{"type": "integer"},
		}},
	}, func(_ context.Context, request *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		var input struct {
			After json.Number `json:"after"`
		}
		decoder := json.NewDecoder(bytes.NewReader(request.Params.Arguments))
		decoder.UseNumber()
		if err := decoder.Decode(&input); err != nil {
			return nil, err
		}
		return &mcp.CallToolResult{
			Content:           []mcp.Content{&mcp.TextContent{Text: input.After.String()}},
			StructuredContent: map[string]any{"text": input.After.String()},
		}, nil
	})
	childClientTransport, childServerTransport := mcp.NewInMemoryTransports()
	childServerSession, err := child.Connect(ctx, childServerTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer childServerSession.Close()
	childClient := mcp.NewClient(&mcp.Implementation{Name: "proxy", Version: "test"}, nil)
	childSession, err := childClient.Connect(ctx, childClientTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer childSession.Close()

	failStatus := false
	updateArgs := []string{}
	startArgs := []string{}
	actionArgs := []string{}
	adminArgs := []string{}
	server, err := NewServer(ctx, childSession, func(_ context.Context, args ...string) (string, error) {
		if len(args) == 0 {
			return "", errors.New("unexpected CLI command")
		}
		if args[0] == "status" && failStatus {
			return "", errors.New("Docker unavailable")
		}
		switch args[0] {
		case "status":
			return "langgraph running", nil
		case "kg-health":
			return "graph healthy", nil
		case "stop":
			return "services stopped", nil
		case "update":
			updateArgs = append([]string{}, args...)
			return "updated", nil
		case "start":
			startArgs = append([]string{}, args...)
			return "services ready", nil
		case "mcp-action":
			actionArgs = append([]string{}, args...)
			return "action complete", nil
		case "opscontrol", "skill", "remove":
			adminArgs = append([]string{}, args...)
			return "admin complete", nil
		default:
			return "", errors.New("unexpected CLI command")
		}
	}, func(context.Context) (*mcp.ClientSession, error) {
		return nil, errors.New("already connected")
	})
	if err != nil {
		t.Fatal(err)
	}
	clientTransport, serverTransport := mcp.NewInMemoryTransports()
	serverSession, err := server.Connect(ctx, serverTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer serverSession.Close()
	client := mcp.NewClient(&mcp.Implementation{Name: "codex", Version: "test"}, nil)
	session, err := client.Connect(ctx, clientTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()

	tools, err := session.ListTools(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(tools.Tools) != 21 {
		t.Fatalf("got %d tools, want three runtime and eighteen host tools", len(tools.Tools))
	}
	for _, tool := range tools.Tools {
		if tool.Name == "decepticon_cli_status" && (tool.Annotations == nil || !tool.Annotations.ReadOnlyHint) {
			t.Fatal("status tool must be read-only")
		}
		if tool.Name == "decepticon_cli_stop" && (tool.Annotations == nil || tool.Annotations.DestructiveHint == nil || !*tool.Annotations.DestructiveHint) {
			t.Fatal("stop tool must be marked destructive")
		}
	}
	forwarded, err := session.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_echo", Arguments: map[string]any{"text": "hello"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if forwarded.StructuredContent.(map[string]any)["text"] != "hello" {
		t.Fatalf("forwarded content = %#v", forwarded.StructuredContent)
	}
	failed, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_fail"})
	if err != nil {
		t.Fatal(err)
	}
	if !failed.IsError {
		t.Fatal("runtime tool error was not preserved")
	}
	direct, err := childSession.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_cursor", Arguments: json.RawMessage(`{"after":9007199254740993}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	if direct.StructuredContent.(map[string]any)["text"] != "9007199254740993" {
		t.Fatalf("direct cursor = %#v", direct.StructuredContent)
	}
	cursor, err := session.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_cursor", Arguments: json.RawMessage(`{"after":9007199254740993}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	if cursor.StructuredContent.(map[string]any)["text"] != "9007199254740993" {
		t.Fatalf("cursor content = %#v", cursor.StructuredContent)
	}
	status, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_status"})
	if err != nil {
		t.Fatal(err)
	}
	if status.StructuredContent.(map[string]any)["output"] != "langgraph running" {
		t.Fatalf("status content = %#v", status.StructuredContent)
	}
	failStatus = true
	status, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_status"})
	if err != nil {
		t.Fatal(err)
	}
	if !status.IsError {
		t.Fatal("host CLI failure was not reported as a tool error")
	}
	health, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_kg_health"})
	if err != nil || health.StructuredContent.(map[string]any)["output"] != "graph healthy" {
		t.Fatalf("kg health = %#v, %v", health, err)
	}
	denied, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_stop"})
	if err != nil || !denied.IsError {
		t.Fatalf("unconfirmed stop = %#v, %v", denied, err)
	}
	stop, err := session.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_cli_stop", Arguments: map[string]any{"confirmed": true},
	})
	if err != nil || stop.StructuredContent.(map[string]any)["output"] != "services stopped" {
		t.Fatalf("stop = %#v, %v", stop, err)
	}
	denied, err = session.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_cli_update", Arguments: map[string]any{"confirmed": true, "channel": "nightly"},
	})
	if err != nil || !denied.IsError {
		t.Fatalf("invalid update channel = %#v, %v", denied, err)
	}
	updated, err := session.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_cli_update", Arguments: map[string]any{
			"confirmed": true, "channel": "stable", "force": true,
		},
	})
	if err != nil || updated.StructuredContent.(map[string]any)["output"] != "updated" {
		t.Fatalf("update = %#v, %v", updated, err)
	}
	if len(updateArgs) != 4 || updateArgs[1] != "--channel" || updateArgs[2] != "stable" || updateArgs[3] != "--force" {
		t.Fatalf("update args = %#v", updateArgs)
	}
	missing, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_start"})
	if err != nil || !missing.IsError {
		t.Fatalf("missing engagement = %#v, %v", missing, err)
	}
	created, err := session.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_cli_create_workspace", Arguments: map[string]any{"engagement": "red-test"},
	})
	if err != nil || created.IsError {
		t.Fatalf("create workspace = %#v, %v", created, err)
	}
	started, err := session.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_cli_start", Arguments: map[string]any{"engagement": "red-test"},
	})
	if err != nil || started.StructuredContent.(map[string]any)["output"] != "services ready" {
		t.Fatalf("start = %#v, %v", started, err)
	}
	if len(startArgs) != 5 || startArgs[1] != "--headless" || startArgs[2] != "--engagement" || startArgs[3] != "red-test" || startArgs[4] != "--no-update" {
		t.Fatalf("start args = %#v", startArgs)
	}
	denied, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_blue", Arguments: map[string]any{"action": "stop"}})
	if err != nil || !denied.IsError {
		t.Fatalf("unconfirmed blue stop = %#v, %v", denied, err)
	}
	denied, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_blue", Arguments: map[string]any{"action": "up", "upstream": "https://public.example:3000"}})
	if err != nil || !denied.IsError {
		t.Fatalf("public blue upstream = %#v, %v", denied, err)
	}
	blue, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_blue", Arguments: map[string]any{"action": "up", "upstream": "127.0.0.1:3000", "log_dir": "/srv/logs"}})
	if err != nil || blue.IsError || len(actionArgs) != 6 || actionArgs[1] != "blue" || actionArgs[2] != "up" || actionArgs[5] != "/srv/logs" {
		t.Fatalf("blue up = %#v, args = %#v, error = %v", blue, actionArgs, err)
	}
	denied, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_web", Arguments: map[string]any{"action": "down"}})
	if err != nil || !denied.IsError {
		t.Fatalf("unconfirmed web down = %#v, %v", denied, err)
	}
	web, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_web", Arguments: map[string]any{"action": "url"}})
	if err != nil || web.IsError || len(actionArgs) != 3 || actionArgs[1] != "web" || actionArgs[2] != "url" {
		t.Fatalf("web url = %#v, args = %#v, error = %v", web, actionArgs, err)
	}
	logs, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_logs", Arguments: map[string]any{"service": "neo4j", "tail": 25}})
	if err != nil || logs.IsError || len(actionArgs) != 5 || actionArgs[3] != "neo4j" || actionArgs[4] != "25" {
		t.Fatalf("logs = %#v, args = %#v, error = %v", logs, actionArgs, err)
	}
	denied, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_opscontrol", Arguments: map[string]any{"action": "uninstall"}})
	if err != nil || !denied.IsError {
		t.Fatalf("unconfirmed opscontrol uninstall = %#v, %v", denied, err)
	}
	ops, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_opscontrol", Arguments: map[string]any{"action": "status"}})
	if err != nil || ops.IsError || len(adminArgs) != 2 || adminArgs[0] != "opscontrol" || adminArgs[1] != "status" {
		t.Fatalf("opscontrol status = %#v, args = %#v, error = %v", ops, adminArgs, err)
	}
	ops, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_opscontrol", Arguments: map[string]any{"action": "install", "confirmed": true}})
	if err != nil || ops.IsError || adminArgs[1] != "install" {
		t.Fatalf("opscontrol install = %#v, args = %#v, error = %v", ops, adminArgs, err)
	}
	denied, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_skill_install", Arguments: map[string]any{"client": "codex", "force": true}})
	if err != nil || !denied.IsError {
		t.Fatalf("unconfirmed skill replacement = %#v, %v", denied, err)
	}
	skill, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_skill_install", Arguments: map[string]any{"client": "codex"}})
	if err != nil || skill.IsError || len(adminArgs) != 4 || adminArgs[0] != "skill" || adminArgs[3] != "codex" {
		t.Fatalf("skill install = %#v, args = %#v, error = %v", skill, adminArgs, err)
	}
	denied, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_skill_install", Arguments: map[string]any{"source": "relative/skill"}})
	if err != nil || !denied.IsError {
		t.Fatalf("relative skill source = %#v, %v", denied, err)
	}
	onboard, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_onboard", Arguments: map[string]any{
		"confirmed": true,
		"settings": map[string]string{
			"DECEPTICON_AUTH_PRIORITY": "openai_api", "OPENAI_API_KEY": "sk-123456789012345678901234",
			"DECEPTICON_TELEMETRY": "off",
		},
	}})
	if err != nil || onboard.IsError || adminArgs[0] != "opscontrol" || adminArgs[1] != "install" {
		t.Fatalf("headless onboard = %#v, args = %#v, error = %v", onboard, adminArgs, err)
	}
	denied, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_onboard", Arguments: map[string]any{
		"reset": true, "settings": map[string]string{"DECEPTICON_TELEMETRY": "research"},
	}})
	if err != nil || !denied.IsError {
		t.Fatalf("unconfirmed onboard reset = %#v, %v", denied, err)
	}
	denied, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_remove", Arguments: map[string]any{"confirmation": "REMOVE DECEPTICON", "delete_workspace": true}})
	if err != nil || !denied.IsError {
		t.Fatalf("wrong delete confirmation = %#v, %v", denied, err)
	}
	removed, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_remove", Arguments: map[string]any{"confirmation": "REMOVE DECEPTICON"}})
	if err != nil || removed.IsError || len(adminArgs) != 3 || adminArgs[0] != "remove" || adminArgs[2] != "--preserve-workspace" {
		t.Fatalf("preserving removal = %#v, args = %#v, error = %v", removed, adminArgs, err)
	}
	removed, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_remove", Arguments: map[string]any{"confirmation": "DELETE ALL DECEPTICON DATA", "delete_workspace": true}})
	if err != nil || removed.IsError || len(adminArgs) != 2 || adminArgs[0] != "remove" || adminArgs[1] != "--yes" {
		t.Fatalf("deleting removal = %#v, args = %#v, error = %v", removed, adminArgs, err)
	}
	duplicate, err := session.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_cli_create_workspace", Arguments: map[string]any{"engagement": "red-test"},
	})
	if err != nil || !duplicate.IsError {
		t.Fatalf("duplicate workspace = %#v, %v", duplicate, err)
	}
	workspaces, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_workspaces"})
	if err != nil || workspaces.IsError {
		t.Fatalf("list workspaces = %#v, %v", workspaces, err)
	}
	listed := workspaces.StructuredContent.(map[string]any)["workspaces"].([]any)
	if len(listed) != 1 || listed[0].(map[string]any)["name"] != "red-test" || listed[0].(map[string]any)["assistant"] != "soundwave" {
		t.Fatalf("listed workspaces = %#v", listed)
	}
	workspace := filepath.Join(os.Getenv("DECEPTICON_HOME"), "workspace", "red-test")
	plan := filepath.Join(workspace, "plan")
	if err := os.WriteFile(filepath.Join(plan, "roe.json"), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	artifacts, err := session.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_cli_list_artifacts", Arguments: map[string]any{"engagement": "red-test"},
	})
	if err != nil || artifacts.IsError || len(artifacts.StructuredContent.(map[string]any)["artifacts"].([]any)) != 1 {
		t.Fatalf("artifacts = %#v, %v", artifacts, err)
	}
	read, err := session.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_cli_read_artifact", Arguments: map[string]any{
			"engagement": "red-test", "path": "plan/roe.json", "max_bytes": 1,
		},
	})
	if err != nil || read.IsError || read.StructuredContent.(map[string]any)["content"] != "{" || read.StructuredContent.(map[string]any)["truncated"] != true {
		t.Fatalf("read artifact = %#v, %v", read, err)
	}
	denied, err = session.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_cli_read_artifact", Arguments: map[string]any{
			"engagement": "red-test", "path": "../.env",
		},
	})
	if err != nil || !denied.IsError {
		t.Fatalf("unsafe read = %#v, %v", denied, err)
	}
	denied, err = session.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_cli_approve_red", Arguments: map[string]any{"engagement": "red-test"},
	})
	if err != nil || !denied.IsError {
		t.Fatalf("unconfirmed approval = %#v, %v", denied, err)
	}
	for _, filename := range []string{
		"threat-profile.json", "conops.json", "deconfliction.json", "contact.json",
		"data-handling.json", "abort.json", "cleanup.json",
	} {
		if err := os.WriteFile(filepath.Join(plan, filename), []byte("{}"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(workspace, ".planning-draft-ready"), []byte("23bb0ad057e96e5098d6c8ef731ce1afadd9e1957f6adf6b720196ad35409418"), 0o600); err != nil {
		t.Fatal(err)
	}
	approved, err := session.CallTool(ctx, &mcp.CallToolParams{
		Name: "decepticon_cli_approve_red", Arguments: map[string]any{"engagement": "red-test", "confirmed": true},
	})
	if err != nil || approved.IsError {
		t.Fatalf("approval = %#v, %v", approved, err)
	}
}

func TestColdServerConnectsRuntimeAfterStartup(t *testing.T) {
	ctx := context.Background()
	child := mcp.NewServer(&mcp.Implementation{Name: "runtime", Version: "test"}, nil)
	mcp.AddTool(child, &mcp.Tool{Name: "decepticon_ready"},
		func(_ context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, commandOutput, error) {
			return nil, commandOutput{Output: "ready"}, nil
		})
	childClientTransport, childServerTransport := mcp.NewInMemoryTransports()
	childServerSession, err := child.Connect(ctx, childServerTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer childServerSession.Close()
	childClient := mcp.NewClient(&mcp.Implementation{Name: "proxy", Version: "test"}, nil)
	childSession, err := childClient.Connect(ctx, childClientTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer childSession.Close()

	ready := false
	server, err := NewServer(ctx, nil, func(_ context.Context, args ...string) (string, error) {
		return args[0], nil
	}, func(context.Context) (*mcp.ClientSession, error) {
		if !ready {
			return nil, errors.New("runtime stopped")
		}
		return childSession, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	clientTransport, serverTransport := mcp.NewInMemoryTransports()
	serverSession, err := server.Connect(ctx, serverTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer serverSession.Close()
	client := mcp.NewClient(&mcp.Implementation{Name: "codex", Version: "test"}, nil)
	session, err := client.Connect(ctx, clientTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()
	tools, err := session.ListTools(ctx, nil)
	if err != nil || len(tools.Tools) != 18 {
		t.Fatalf("cold tools = %#v, %v", tools, err)
	}
	failed, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_connect_runtime"})
	if err != nil || !failed.IsError {
		t.Fatalf("stopped runtime = %#v, %v", failed, err)
	}
	ready = true
	connected, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_cli_connect_runtime"})
	if err != nil || connected.IsError {
		t.Fatalf("connect runtime = %#v, %v", connected, err)
	}
	tools, err = session.ListTools(ctx, nil)
	if err != nil || len(tools.Tools) != 19 {
		t.Fatalf("connected tools = %#v, %v", tools, err)
	}
	result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_ready"})
	if err != nil || result.StructuredContent.(map[string]any)["output"] != "ready" {
		t.Fatalf("runtime call = %#v, %v", result, err)
	}
}
