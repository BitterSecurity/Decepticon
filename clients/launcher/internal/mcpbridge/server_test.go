package mcpbridge

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
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
	if len(tools.Tools) != 8 {
		t.Fatalf("got %d tools, want three runtime and five host tools", len(tools.Tools))
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
	if err != nil || len(tools.Tools) != 5 {
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
	if err != nil || len(tools.Tools) != 6 {
		t.Fatalf("connected tools = %#v, %v", tools, err)
	}
	result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "decepticon_ready"})
	if err != nil || result.StructuredContent.(map[string]any)["output"] != "ready" {
		t.Fatalf("runtime call = %#v, %v", result, err)
	}
}
