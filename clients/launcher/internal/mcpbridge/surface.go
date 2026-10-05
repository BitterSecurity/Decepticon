package mcpbridge

import (
	"context"
	"fmt"
	"net/url"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type blueInput struct {
	Action    string `json:"action" jsonschema:"up, status, verify, or stop"`
	Upstream  string `json:"upstream,omitempty" jsonschema:"local HTTP service, required for up"`
	LogDir    string `json:"log_dir,omitempty" jsonschema:"optional absolute host log directory for up"`
	Confirmed bool   `json:"confirmed,omitempty" jsonschema:"required for stopping the sensor"`
}

type webInput struct {
	Action    string `json:"action" jsonschema:"up, down, or url"`
	Confirmed bool   `json:"confirmed,omitempty" jsonschema:"required for stopping the dashboard"`
}

type logsInput struct {
	Service string `json:"service,omitempty" jsonschema:"langgraph, litellm, postgres, neo4j, sandbox, web, or cli"`
	Tail    int    `json:"tail,omitempty" jsonschema:"number of recent lines, 1 to 200"`
}

func registerSurfaceTools(server *mcp.Server, run CommandRunner, closedWorld bool) {
	destructive := true
	mcp.AddTool(server, &mcp.Tool{
		Name:        "decepticon_cli_blue",
		Description: "Start, inspect, verify coverage, or stop the local Blue Cell sensor. Up requires a local HTTP service; route traffic through port 18080.",
		Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: &closedWorld},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input blueInput) (*mcp.CallToolResult, commandOutput, error) {
		if input.Action != "up" && input.Action != "status" && input.Action != "verify" && input.Action != "stop" {
			return nil, commandOutput{}, fmt.Errorf("blue action must be up, status, verify, or stop")
		}
		if input.Action == "stop" && !input.Confirmed {
			return nil, commandOutput{}, fmt.Errorf("stopping Blue Cell requires operator confirmation")
		}
		if input.Action != "up" && (input.Upstream != "" || input.LogDir != "") {
			return nil, commandOutput{}, fmt.Errorf("upstream and log_dir apply only to blue up")
		}
		args := []string{"mcp-action", "blue", input.Action}
		if input.Action == "up" {
			if input.Upstream == "" {
				return nil, commandOutput{}, fmt.Errorf("blue up requires a local upstream")
			}
			if err := validateLocalUpstream(input.Upstream); err != nil {
				return nil, commandOutput{}, err
			}
			if input.LogDir != "" && !filepath.IsAbs(input.LogDir) {
				return nil, commandOutput{}, fmt.Errorf("log_dir must be an absolute host path")
			}
			args = append(args, input.Upstream)
			if input.LogDir != "" {
				args = append(args, "--logs", input.LogDir)
			}
		}
		output, err := run(ctx, args...)
		return nil, commandOutput{Output: output}, err
	})
	mcp.AddTool(server, &mcp.Tool{
		Name:        "decepticon_cli_web",
		Description: "Start or stop the web dashboard, or return its URL.",
		Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: &closedWorld},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input webInput) (*mcp.CallToolResult, commandOutput, error) {
		if input.Action != "up" && input.Action != "down" && input.Action != "url" {
			return nil, commandOutput{}, fmt.Errorf("web action must be up, down, or url")
		}
		if input.Action == "down" && !input.Confirmed {
			return nil, commandOutput{}, fmt.Errorf("stopping web dashboard requires operator confirmation")
		}
		output, err := run(ctx, "mcp-action", "web", input.Action)
		return nil, commandOutput{Output: output}, err
	})
	mcp.AddTool(server, &mcp.Tool{
		Name:        "decepticon_cli_logs",
		Description: "Read a bounded recent service log sample without following the stream.",
		Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true, OpenWorldHint: &closedWorld},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input logsInput) (*mcp.CallToolResult, commandOutput, error) {
		service := input.Service
		if service == "" {
			service = "langgraph"
		}
		allowed := map[string]bool{"langgraph": true, "litellm": true, "postgres": true, "neo4j": true, "sandbox": true, "web": true, "cli": true}
		if !allowed[service] {
			return nil, commandOutput{}, fmt.Errorf("unsupported log service %q", service)
		}
		tail := input.Tail
		if tail == 0 {
			tail = 50
		}
		if tail < 1 || tail > 200 {
			return nil, commandOutput{}, fmt.Errorf("log tail must be between 1 and 200")
		}
		output, err := run(ctx, "mcp-action", "logs", "tail", service, strconv.Itoa(tail))
		return nil, commandOutput{Output: output}, err
	})
}

func validateLocalUpstream(raw string) error {
	address := raw
	if !strings.Contains(raw, "://") {
		address = "http://" + raw
	}
	parsed, err := url.Parse(address)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Port() == "" ||
		(parsed.Hostname() != "localhost" && parsed.Hostname() != "127.0.0.1" && parsed.Hostname() != "::1") ||
		parsed.User != nil || parsed.Path != "" && parsed.Path != "/" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return fmt.Errorf("blue upstream must be a local HTTP origin such as 127.0.0.1:3000")
	}
	return nil
}
