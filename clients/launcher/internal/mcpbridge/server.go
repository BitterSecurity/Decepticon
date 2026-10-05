package mcpbridge

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"sync"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type CommandRunner func(context.Context, ...string) (string, error)

type commandOutput struct {
	Output string `json:"output"`
}

type confirmedAction struct {
	Confirmed bool `json:"confirmed" jsonschema:"true only after the operator authorizes this action"`
}

type updateInput struct {
	Confirmed bool   `json:"confirmed" jsonschema:"true only after the operator authorizes updating Decepticon"`
	Channel   string `json:"channel,omitempty" jsonschema:"stable or latest; empty uses installed configuration"`
	Force     bool   `json:"force,omitempty" jsonschema:"refresh files and images even if version is unchanged"`
}

func LocalCommand(ctx context.Context, args ...string) (string, error) {
	bin, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("locate decepticon launcher: %w", err)
	}
	output, err := exec.CommandContext(ctx, bin, args...).CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("decepticon %s: %w: %s", args[0], err, output)
	}
	return string(output), nil
}

func NewServer(ctx context.Context, runtime *mcp.ClientSession, run CommandRunner, connect func(context.Context) (*mcp.ClientSession, error)) (*mcp.Server, error) {
	server := mcp.NewServer(&mcp.Implementation{Name: "decepticon", Version: "1"}, nil)
	hostNames := map[string]bool{
		"decepticon_cli_status": true, "decepticon_cli_kg_health": true,
		"decepticon_cli_stop": true, "decepticon_cli_update": true,
		"decepticon_cli_connect_runtime": true,
	}
	registerRuntime := func(session *mcp.ClientSession) error {
		return registerRuntimeTools(ctx, server, session, hostNames)
	}
	if runtime != nil {
		if err := registerRuntime(runtime); err != nil {
			return nil, err
		}
	}
	var connectMu sync.Mutex
	closedWorld := false
	mcp.AddTool(server, &mcp.Tool{
		Name:        "decepticon_cli_connect_runtime",
		Description: "Connect engagement, plugin, and Blue Cell observation tools after Decepticon services are running. Call after starting services if those tools are absent.",
		Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true, OpenWorldHint: &closedWorld},
	}, func(callCtx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, commandOutput, error) {
		connectMu.Lock()
		defer connectMu.Unlock()
		if runtime != nil {
			return nil, commandOutput{Output: "Decepticon runtime tools are connected"}, nil
		}
		session, err := connect(callCtx)
		if err != nil {
			return nil, commandOutput{}, err
		}
		if err := registerRuntime(session); err != nil {
			session.Close()
			return nil, commandOutput{}, err
		}
		runtime = session
		return nil, commandOutput{Output: "Decepticon runtime tools are connected"}, nil
	})

	destructive := true
	mcp.AddTool(server, &mcp.Tool{
		Name:        "decepticon_cli_status",
		Description: "Show the installed Decepticon service status using the host launcher.",
		Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true, OpenWorldHint: &closedWorld},
	}, func(callCtx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, commandOutput, error) {
		output, err := run(callCtx, "status")
		return nil, commandOutput{Output: output}, err
	})
	mcp.AddTool(server, &mcp.Tool{
		Name:        "decepticon_cli_kg_health",
		Description: "Check the installed Decepticon knowledge graph connection.",
		Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true, OpenWorldHint: &closedWorld},
	}, func(callCtx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, commandOutput, error) {
		output, err := run(callCtx, "kg-health")
		return nil, commandOutput{Output: output}, err
	})
	mcp.AddTool(server, &mcp.Tool{
		Name:        "decepticon_cli_stop",
		Description: "Stop all installed Decepticon services after operator authorization.",
		Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: &closedWorld},
	}, func(callCtx context.Context, _ *mcp.CallToolRequest, input confirmedAction) (*mcp.CallToolResult, commandOutput, error) {
		if !input.Confirmed {
			return nil, commandOutput{}, fmt.Errorf("stopping services requires operator confirmation")
		}
		output, err := run(callCtx, "stop")
		return nil, commandOutput{Output: output}, err
	})
	mcp.AddTool(server, &mcp.Tool{
		Name:        "decepticon_cli_update",
		Description: "Update the installed Decepticon release after operator authorization.",
		Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: &closedWorld},
	}, func(callCtx context.Context, _ *mcp.CallToolRequest, input updateInput) (*mcp.CallToolResult, commandOutput, error) {
		if !input.Confirmed {
			return nil, commandOutput{}, fmt.Errorf("updating Decepticon requires operator confirmation")
		}
		if input.Channel != "" && input.Channel != "stable" && input.Channel != "latest" {
			return nil, commandOutput{}, fmt.Errorf("channel must be stable or latest")
		}
		args := []string{"update"}
		if input.Channel != "" {
			args = append(args, "--channel", input.Channel)
		}
		if input.Force {
			args = append(args, "--force")
		}
		output, err := run(callCtx, args...)
		return nil, commandOutput{Output: output}, err
	})
	return server, nil
}

func registerRuntimeTools(ctx context.Context, server *mcp.Server, runtime *mcp.ClientSession, hostNames map[string]bool) error {
	var tools []*mcp.Tool
	for tool, err := range runtime.Tools(ctx, nil) {
		if err != nil {
			return fmt.Errorf("list Decepticon runtime tools: %w", err)
		}
		if hostNames[tool.Name] {
			return fmt.Errorf("runtime tool %q conflicts with host control", tool.Name)
		}
		tools = append(tools, tool)
	}
	for _, tool := range tools {
		forwarded := *tool
		server.AddTool(&forwarded, func(callCtx context.Context, request *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			args := request.Params.Arguments
			if len(args) == 0 {
				args = json.RawMessage("{}")
			}
			return runtime.CallTool(callCtx, &mcp.CallToolParams{Name: forwarded.Name, Arguments: args})
		})
	}
	return nil
}
