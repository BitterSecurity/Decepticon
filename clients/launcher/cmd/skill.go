package cmd

import (
	"fmt"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/agentskill"
	"github.com/spf13/cobra"
)

var skillOptions agentskill.Options

var skillCmd = &cobra.Command{
	Use:   "skill",
	Short: "Install Decepticon guidance for coding agents",
}

var skillInstallCmd = &cobra.Command{
	Use:   "install",
	Short: "Install the Decepticon Agent Skill for Claude Code or Codex",
	Args:  cobra.NoArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		skillOptions.Version = version
		paths, err := agentskill.Install(cmd.Context(), skillOptions)
		for _, path := range paths {
			fmt.Fprintln(cmd.OutOrStdout(), "Installed Decepticon Skill at", path)
		}
		return err
	},
}

func init() {
	skillInstallCmd.Flags().StringVar(&skillOptions.Client, "client", "both", "Coding agent: codex, claude, or both")
	skillInstallCmd.Flags().StringVar(&skillOptions.Source, "from", "", "Install from a local Decepticon Skill directory")
	skillInstallCmd.Flags().BoolVar(&skillOptions.Force, "force", false, "Back up and replace a modified Decepticon Skill")
	skillCmd.AddCommand(skillInstallCmd)
	rootCmd.AddCommand(skillCmd)
}
