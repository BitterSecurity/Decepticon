package cmd

import (
	"fmt"
	"os"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/ui"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/updater"
	"github.com/spf13/cobra"
)

var version = "dev"

var rootCmd = &cobra.Command{
	Use:   "decepticon",
	Short: "Decepticon — Autonomous Hacking Agent for Red Team",
	Long:  ui.RenderBanner() + "\n" + ui.Dim.Render("Autonomous Hacking Agent for Red Team"),
	CompletionOptions: cobra.CompletionOptions{
		HiddenDefaultCmd: true,
	},
	SilenceUsage:  true,
	SilenceErrors: true,
}

func Execute() {
	updater.CleanupOldBinary()
	command, err := rootCmd.ExecuteC()
	if err != nil {
		if command == mcpServeCmd {
			fmt.Fprintln(os.Stderr, err)
		} else {
			ui.Error(err.Error())
		}
		os.Exit(1)
	}
}

func init() {
	rootCmd.Version = version
	rootCmd.SetVersionTemplate(fmt.Sprintf("Decepticon %s\n", version))
}
