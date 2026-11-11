package cmd

import (
	"fmt"
	"os"
	"strings"

	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/compose"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/config"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/engagement"
	"github.com/spf13/cobra"
)

var documentedLogServices = []string{"langgraph", "litellm", "postgres", "neo4j", "sandbox"}

var logsCmd = &cobra.Command{
	Use:   "logs [service]",
	Short: "Follow service logs, or replay an engagement transcript",
	Long: `Two modes:

Service mode (default): follow logs for one service in the current Compose
stack. The default service is langgraph. Documented services: langgraph,
litellm, postgres, neo4j, sandbox. Additional stack services (such as
cli or c2-sliver) are accepted when present in Compose configuration.

Engagement mode (--engagement): replay the conversation transcript of a
(possibly background) engagement from its LangGraph thread state — the same
surface the MCP decepticon_transcript tool reads. Read-only; the engagement
does not need to be running.`,
	Example: `  decepticon logs
  decepticon logs litellm
  decepticon logs cli
  decepticon logs --engagement my-engagement
  decepticon logs --engagement my-engagement --limit 20
  decepticon logs --engagement 01a16679-e6af-4d09-a238-57ae6af4e79c`,
	Args: cobra.MaximumNArgs(1),
	ValidArgsFunction: func(cmd *cobra.Command, args []string, toComplete string) ([]string, cobra.ShellCompDirective) {
		if len(args) > 0 {
			return nil, cobra.ShellCompDirectiveNoFileComp
		}
		names, err := compose.New().ServiceNames()
		if err != nil {
			names = documentedLogServices
		}
		return names, cobra.ShellCompDirectiveNoFileComp
	},
	RunE: func(cmd *cobra.Command, args []string) error {
		engagementName, err := cmd.Flags().GetString("engagement")
		if err != nil {
			return err
		}
		if engagementName != "" {
			if len(args) > 0 {
				return fmt.Errorf("a service argument and --engagement are mutually exclusive")
			}
			return runEngagementTranscript(cmd, engagementName)
		}

		service := "langgraph"
		if len(args) > 0 {
			service = args[0]
		}
		if !isDocumentedLogService(service) {
			names, err := compose.New().ServiceNames()
			if err != nil {
				return fmt.Errorf("validate service %q: %w", service, err)
			}
			found := false
			for _, name := range names {
				if name == service {
					found = true
					break
				}
			}
			if !found {
				return fmt.Errorf("unknown service %q; documented services: %s", service, strings.Join(documentedLogServices, ", "))
			}
		}
		return compose.New().Logs(service)
	},
}

// runEngagementTranscript resolves the engagement (or explicit thread id) to
// its LangGraph thread and prints the trailing transcript window. The
// endpoint resolution matches the readiness probe: process env overrides
// the .env file, matching Compose interpolation.
func runEngagementTranscript(cmd *cobra.Command, target string) error {
	limit, err := cmd.Flags().GetInt("limit")
	if err != nil {
		return err
	}
	env, envErr := config.LoadEnv(config.EnvPath())
	if envErr != nil {
		env = map[string]string{}
	}
	baseURL := langgraphBaseURL(env)
	client := engagement.DefaultLogsClient()
	ctx := cmd.Context()

	threadID, err := engagement.ResolveThreadID(ctx, client, baseURL, target)
	if err != nil {
		return err
	}
	state, err := engagement.FetchThreadState(ctx, client, baseURL, threadID)
	if err != nil {
		return err
	}
	cmd.Printf("engagement %s (thread %s) — %d message(s), plan revision %d\n\n",
		state.EngagementName, state.ThreadID, len(state.Messages), state.PlanRevision)
	for _, message := range engagement.ReplayLimit(state.Messages, limit) {
		cmd.Printf("[%s] %s\n", message.Role, strings.ReplaceAll(message.Content, "\n", "\n  "))
	}
	return nil
}

// langgraphBaseURL resolves the stack's host LangGraph endpoint the same way
// the readiness probe does.
func langgraphBaseURL(env map[string]string) string {
	port := config.Get(env, "LANGGRAPH_PORT", "2024")
	if processPort, set := os.LookupEnv("LANGGRAPH_PORT"); set {
		if processPort == "" {
			port = "2024"
		} else {
			port = processPort
		}
	}
	return "http://127.0.0.1:" + port
}

func isDocumentedLogService(name string) bool {
	for _, known := range documentedLogServices {
		if name == known {
			return true
		}
	}
	return false
}

func init() {
	logsCmd.Flags().String("engagement", "", "Replay an engagement's transcript (name or thread id) instead of following service logs")
	logsCmd.Flags().Int("limit", 50, "Print the trailing N messages (engagement mode)")
	_ = logsCmd.Flags().SetAnnotation("engagement", cobra.BashCompFilenameExt, []string{""})
	rootCmd.AddCommand(logsCmd)
}
