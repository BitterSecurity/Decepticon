package cmd

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/compose"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/config"
	"github.com/spf13/cobra"
)

var mcpCmd = &cobra.Command{
	Use:   "mcp",
	Short: "Connect coding agents to a running Decepticon instance",
}

var mcpServeCmd = &cobra.Command{
	Use:   "serve",
	Short: "Serve Decepticon MCP tools over stdio",
	Long:  "Serve Decepticon MCP tools over stdio for Claude Code, Codex, or another MCP client. Start Decepticon first.",
	Args:  cobra.NoArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		home := config.DecepticonHome()
		for _, name := range []string{"docker-compose.yml", ".env"} {
			if _, err := os.Stat(filepath.Join(home, name)); err != nil {
				return fmt.Errorf("Decepticon is not installed at %s; run decepticon onboard first: %w", home, err)
			}
		}
		return compose.New().RunMCPStdio()
	},
}

func init() {
	mcpCmd.AddCommand(mcpServeCmd)
	rootCmd.AddCommand(mcpCmd)
}
