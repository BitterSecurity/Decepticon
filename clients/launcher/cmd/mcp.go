package cmd

import (
	"context"
	"fmt"
	"os"
	"strconv"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/compose"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/mcpbridge"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/spf13/cobra"
)

var mcpCmd = &cobra.Command{
	Use:   "mcp",
	Short: "Connect coding agents to a running Decepticon instance",
}

var mcpServeCmd = &cobra.Command{
	Use:   "serve",
	Short: "Serve Decepticon MCP tools over stdio",
	Long:  "Serve Decepticon MCP tools over stdio for Claude Code, Codex, or another MCP client.",
	Args:  cobra.NoArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		return serveMCP(cmd.Context(), compose.New())
	},
}

var mcpActionCmd = &cobra.Command{
	Use:    "mcp-action <blue|web|logs> <action> [args...]",
	Hidden: true,
	Args:   cobra.MinimumNArgs(2),
	RunE: func(cmd *cobra.Command, args []string) error {
		stack := compose.New()
		var output string
		var err error
		switch args[0] {
		case "blue", "web":
			output, err = stack.MCPAction(cmd.Context(), args[0], args[1], args[2:]...)
		case "logs":
			if len(args) != 4 || args[1] != "tail" {
				return fmt.Errorf("logs action requires tail, service, and count")
			}
			allowed := map[string]bool{"langgraph": true, "litellm": true, "postgres": true, "neo4j": true, "sandbox": true, "web": true, "cli": true}
			if !allowed[args[2]] {
				return fmt.Errorf("unsupported log service %q", args[2])
			}
			count, parseErr := strconv.Atoi(args[3])
			if parseErr != nil || count < 1 || count > 200 {
				return fmt.Errorf("log tail must be between 1 and 200")
			}
			output, err = stack.MCPLogs(cmd.Context(), args[2], count)
		default:
			return fmt.Errorf("unsupported MCP action %q", args[0])
		}
		if err != nil {
			return err
		}
		fmt.Fprint(cmd.OutOrStdout(), output)
		return nil
	},
}

func serveMCP(ctx context.Context, stack *compose.Compose) error {
	client := mcp.NewClient(&mcp.Implementation{Name: "decepticon-launcher", Version: version}, nil)
	connect := func(ctx context.Context) (*mcp.ClientSession, error) {
		return client.Connect(ctx, &mcp.CommandTransport{Command: stack.MCPCommand()}, nil)
	}
	runtime, err := connect(ctx)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Decepticon runtime unavailable; host MCP tools remain available: %v\n", err)
	}
	defer func() {
		if runtime != nil {
			runtime.Close()
		}
	}()
	server, err := mcpbridge.NewServer(ctx, runtime, mcpbridge.LocalCommand, func(ctx context.Context) (*mcp.ClientSession, error) {
		session, err := connect(ctx)
		if err == nil {
			runtime = session
		}
		return session, err
	})
	if err != nil {
		return err
	}
	return server.Run(ctx, &mcp.StdioTransport{})
}

func init() {
	mcpCmd.AddCommand(mcpServeCmd)
	rootCmd.AddCommand(mcpCmd)
	rootCmd.AddCommand(mcpActionCmd)
}
