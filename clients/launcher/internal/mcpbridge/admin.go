package mcpbridge

import (
	"context"
	"fmt"
	"path/filepath"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type opscontrolInput struct {
	Action    string `json:"action" jsonschema:"status, install, or uninstall"`
	Confirmed bool   `json:"confirmed,omitempty" jsonschema:"required for install or uninstall"`
}

type skillInstallInput struct {
	Client    string `json:"client,omitempty" jsonschema:"codex, claude, or both; defaults to both"`
	Source    string `json:"source,omitempty" jsonschema:"optional absolute local skill directory for development builds"`
	Force     bool   `json:"force,omitempty" jsonschema:"back up and replace an existing modified skill"`
	Confirmed bool   `json:"confirmed,omitempty" jsonschema:"required when force is true"`
}

type removeInput struct {
	Confirmation    string `json:"confirmation" jsonschema:"exact operator confirmation phrase"`
	DeleteWorkspace bool   `json:"delete_workspace,omitempty" jsonschema:"false backs up the workspace before removal; true deletes it"`
}

func registerRemoveTool(server *mcp.Server, run CommandRunner, closedWorld bool) {
	destructive := true
	mcp.AddTool(server, &mcp.Tool{
		Name:        "decepticon_cli_remove",
		Description: "Uninstall the local stack and launcher. By default, back up the workspace; confirm with REMOVE DECEPTICON. To delete workspace data too, set delete_workspace=true and confirm with DELETE ALL DECEPTICON DATA.",
		Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: &closedWorld},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input removeInput) (*mcp.CallToolResult, commandOutput, error) {
		phrase := "REMOVE DECEPTICON"
		if input.DeleteWorkspace {
			phrase = "DELETE ALL DECEPTICON DATA"
		}
		if input.Confirmation != phrase {
			return nil, commandOutput{}, fmt.Errorf("removal requires the exact confirmation phrase %q", phrase)
		}
		args := []string{"remove", "--yes"}
		if !input.DeleteWorkspace {
			args = append(args, "--preserve-workspace")
		}
		output, err := run(ctx, args...)
		return nil, commandOutput{Output: output}, err
	})
}

func registerAdminTools(server *mcp.Server, run CommandRunner, closedWorld bool) {
	destructive := true
	mcp.AddTool(server, &mcp.Tool{
		Name:        "decepticon_cli_opscontrol",
		Description: "Inspect, install, or uninstall the local opscontrol background service.",
		Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: &closedWorld},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input opscontrolInput) (*mcp.CallToolResult, commandOutput, error) {
		if input.Action != "status" && input.Action != "install" && input.Action != "uninstall" {
			return nil, commandOutput{}, fmt.Errorf("opscontrol action must be status, install, or uninstall")
		}
		if input.Action != "status" && !input.Confirmed {
			return nil, commandOutput{}, fmt.Errorf("opscontrol %s requires operator confirmation", input.Action)
		}
		output, err := run(ctx, "opscontrol", input.Action)
		return nil, commandOutput{Output: output}, err
	})
	mcp.AddTool(server, &mcp.Tool{
		Name:        "decepticon_cli_skill_install",
		Description: "Install the version-matched Decepticon Agent Skill for Codex, Claude Code, or both.",
		Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: &closedWorld},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input skillInstallInput) (*mcp.CallToolResult, commandOutput, error) {
		client := input.Client
		if client == "" {
			client = "both"
		}
		if client != "codex" && client != "claude" && client != "both" {
			return nil, commandOutput{}, fmt.Errorf("client must be codex, claude, or both")
		}
		if input.Force && !input.Confirmed {
			return nil, commandOutput{}, fmt.Errorf("replacing a modified skill requires operator confirmation")
		}
		args := []string{"skill", "install", "--client", client}
		if input.Source != "" {
			if !filepath.IsAbs(input.Source) {
				return nil, commandOutput{}, fmt.Errorf("source must be an absolute local directory")
			}
			args = append(args, "--from", input.Source)
		}
		if input.Force {
			args = append(args, "--force")
		}
		output, err := run(ctx, args...)
		return nil, commandOutput{Output: output}, err
	})
}
