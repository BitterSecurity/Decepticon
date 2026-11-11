package compose

import (
	"encoding/json"
	"fmt"
	"net"
	"os"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/config"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/ui"
)

// Only retry daemon-confirmed loopback TCP bind failures. Never stop the owner.
var allocatedPort = regexp.MustCompile(`(?:Bind for 127\.0\.0\.1:|listen tcp 127\.0\.0\.1:)([0-9]+)(?: failed: port is already allocated|: bind: address already in use)`)

var publishedPortKeys = map[string]map[int]string{
	"litellm":   {4000: "LITELLM_PORT"},
	"postgres":  {5432: "POSTGRES_PORT"},
	"neo4j":     {7474: "NEO4J_HTTP_PORT", 7687: "NEO4J_BOLT_PORT"},
	"langgraph": {2024: "LANGGRAPH_PORT"},
	"skillogy":  {9100: "SKILLOGY_REST_PORT"},
}

// Recover from the effective Compose configuration, including overrides and
// shell interpolation. Internal service DNS/ports never change; re-running up
// rewires host URLs interpolated into consumers (the CLI) from the saved keys.
func (c *Compose) recoverAllocatedPort(args []string, port string) error {
	var rendered struct {
		Services map[string]struct {
			Ports []struct {
				HostIP    string `json:"host_ip"`
				Published string `json:"published"`
				Target    int    `json:"target"`
				Protocol  string `json:"protocol"`
			} `json:"ports"`
		} `json:"services"`
	}
	configArgs := append(c.baseArgs(), args[:slices.Index(args, "up")]...)
	output, _, err := c.networkRecoveryOutput(append(configArgs, "config", "--format", "json"))
	if err != nil {
		return fmt.Errorf("read published ports: %w", err)
	}
	if err := json.Unmarshal(output, &rendered); err != nil {
		return fmt.Errorf("decode published ports: %w", err)
	}
	reserved := map[string]bool{}
	key, service := "", ""
	for name, svc := range rendered.Services {
		for _, binding := range svc.Ports {
			reserved[binding.Published] = true
			if binding.Published != port || binding.HostIP != "127.0.0.1" || binding.Protocol != "tcp" {
				continue
			}
			candidate := publishedPortKeys[name][binding.Target]
			if candidate == "" || key != "" {
				return fmt.Errorf("cannot safely identify a unique configurable host port %s", port)
			}
			key, service = candidate, name
		}
	}
	if key == "" {
		return fmt.Errorf("host port %s is not a launcher-managed loopback TCP binding", port)
	}
	first, err := strconv.Atoi(port)
	if err != nil {
		return err
	}
	for candidate := first + 1; candidate <= 65535; candidate++ {
		value := strconv.Itoa(candidate)
		if reserved[value] {
			continue
		}
		listener, err := net.Listen("tcp4", "127.0.0.1:"+value)
		if err != nil {
			continue
		}
		// A custom overlay may hardcode this binding instead of using the
		// supported setting. Prove the key rewires the effective config before
		// changing the installation or reporting a successful remap.
		previousEnv := c.ExtraEnv
		c.ExtraEnv = append(slices.Clone(previousEnv), key+"="+value)
		changed, _, renderErr := c.networkRecoveryOutput(append(configArgs, "config", "--format", "json"))
		c.ExtraEnv = previousEnv
		if renderErr != nil {
			listener.Close()
			return fmt.Errorf("validate remapped port: %w", renderErr)
		}
		var updated = rendered
		if err := json.Unmarshal(changed, &updated); err != nil {
			listener.Close()
			return err
		}
		rewired := false
		for _, binding := range updated.Services[service].Ports {
			if publishedPortKeys[service][binding.Target] == key && binding.Published == value && binding.HostIP == "127.0.0.1" && binding.Protocol == "tcp" {
				rewired = true
			}
		}
		if !rewired {
			listener.Close()
			return fmt.Errorf("%s does not control this binding; check Compose overrides", key)
		}
		// Persist atomically before retrying, preserving all credentials/comments.
		err = config.SetEnvKey(c.EnvFile, key, value)
		listener.Close()
		if err != nil {
			return fmt.Errorf("save %s: %w", key, err)
		}
		// Override inherited shell values for every subsequent call on this object.
		c.ExtraEnv = append(c.ExtraEnv, key+"="+value)
		if err := os.Setenv(key, value); err != nil {
			return err
		}
		ui.Info(fmt.Sprintf("%s port %s is in use; using 127.0.0.1:%s (%s saved in .env).", service, port, value, key))
		return nil
	}
	return fmt.Errorf("no available host port above %s for %s", port, service)
}

// RefreshPorts updates an already loaded startup config after automatic remaps.
// Compose uses ExtraEnv above; host probes and connection hints use this map.
func (c *Compose) RefreshPorts(env map[string]string) {
	saved, _ := config.LoadEnv(c.EnvFile)
	for _, keys := range publishedPortKeys {
		for _, key := range keys {
			if value, ok := saved[key]; ok {
				env[key] = value
			}
			if value, ok := os.LookupEnv(key); ok {
				env[key] = value
			}
			for _, entry := range c.ExtraEnv {
				if value, ok := strings.CutPrefix(entry, key+"="); ok {
					env[key] = value
				}
			}
		}
	}
}
