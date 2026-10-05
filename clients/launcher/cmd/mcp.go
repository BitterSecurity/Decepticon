package cmd

import (
	"context"
	"fmt"
	"os"

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
}
