package mcpbridge

import (
	"context"
	"fmt"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/config"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/engagement"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type importTargetInput struct {
	Engagement string `json:"engagement" jsonschema:"existing local engagement workspace slug"`
	Source     string `json:"source" jsonschema:"absolute local source directory"`
	Name       string `json:"name" jsonschema:"unique 3-64 character target snapshot slug"`
	Confirmed  bool   `json:"confirmed" jsonschema:"true after the operator approves copying this directory into the engagement workspace"`
}

func registerTargetTool(server *mcp.Server, closedWorld bool) {
	mcp.AddTool(server, &mcp.Tool{
		Name:        "decepticon_cli_import_target",
		Description: "Copy an authorized local source tree into an engagement workspace and return the sandbox-visible /workspace/targets path. Limits: 10000 files, 100 MiB total, 16 MiB per file; symlinks are rejected.",
		Annotations: &mcp.ToolAnnotations{OpenWorldHint: &closedWorld},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input importTargetInput) (*mcp.CallToolResult, engagement.TargetImport, error) {
		if !input.Confirmed {
			return nil, engagement.TargetImport{}, fmt.Errorf("target import requires operator confirmation")
		}
		imported, err := engagement.ImportTarget(ctx, config.DecepticonHome(), input.Engagement, input.Source, input.Name)
		return nil, imported, err
	})
}
