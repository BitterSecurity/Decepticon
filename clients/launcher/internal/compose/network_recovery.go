package compose

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"regexp"
	"slices"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/opscontrol"
)

var missingNetwork = regexp.MustCompile(`(?m)^Error response from daemon: [^\r\n]*\bnetwork ([0-9a-f]{64}) (?:not found|does not exist)\r?$`)

var absentNetwork = regexp.MustCompile(`(?m)^Error response from daemon: network ([0-9a-f]{64}) not found\r?$`)

var (
	errUnsafeNetwork      = errors.New("refusing recovery of a network that is not empty and managed by this Compose project")
	errNetworkDiagnostics = errors.New("network recovery command output exceeded its limit")
)

type diagnosticTail struct {
	bytes     []byte
	limit     int
	truncated bool
}

func (b *diagnosticTail) Write(p []byte) (int, error) {
	n := len(p)
	if len(b.bytes)+n > b.limit {
		b.truncated = true
		if n >= b.limit {
			b.bytes = append(b.bytes[:0], p[n-b.limit:]...)
			return n, nil
		}
		b.bytes = b.bytes[len(b.bytes)+n-b.limit:]
	}
	b.bytes = append(b.bytes, p...)
	return n, nil
}

type composeNetwork struct {
	Name     string `json:"name"`
	External bool   `json:"external"`
}

func (c *Compose) upWithNetworkRecovery(args []string) error {
	if c.Runtime.Name != "docker" {
		return c.run(args, false)
	}
	var networks map[string]composeNetwork
	repaired := make(map[string]bool)
	for {
		diagnostics := diagnosticTail{limit: 64 << 10}
		err := c.runWithStderr(args, false, io.MultiWriter(os.Stderr, &diagnostics))
		if err == nil {
			return nil
		}
		match := missingNetwork.FindSubmatch(diagnostics.bytes)
		if match == nil {
			return err
		}
		id := string(match[1])
		if repaired[id] {
			return err
		}
		if networks == nil {
			var config struct {
				Networks map[string]composeNetwork `json:"networks"`
			}
			configArgs := append(c.baseArgs(), args[:slices.Index(args, "up")]...)
			output, _, configErr := c.networkRecoveryOutput(append(configArgs, "config", "--format", "json"))
			if configErr != nil {
				return fmt.Errorf("%w; reading Compose networks: %w", err, configErr)
			}
			if configErr := json.Unmarshal(output, &config); configErr != nil {
				return fmt.Errorf("%w; decoding Compose networks: %w", err, configErr)
			}
			networks = make(map[string]composeNetwork)
			for key, network := range config.Networks {
				if !network.External {
					networks[key] = network
				}
			}
		}
		if len(repaired) >= len(networks) {
			return err
		}
		if repairErr := c.repairMissingNetwork(id, networks); repairErr != nil {
			return fmt.Errorf("%w; missing-network recovery: %w", err, repairErr)
		}
		repaired[id] = true
		if len(repaired) == 1 {
			// Keep service arguments last (UpDatabases), and retain selected profiles.
			args = slices.Insert(slices.Clone(args), slices.Index(args, "up")+1, "--force-recreate")
		}
	}
}

func (c *Compose) repairMissingNetwork(id string, networks map[string]composeNetwork) error {
	output, diagnostics, err := c.networkRecoveryOutput([]string{"network", "inspect", id})
	if err != nil {
		match := absentNetwork.FindSubmatch(diagnostics)
		if match != nil && string(match[1]) == id {
			return nil
		}
		return fmt.Errorf("inspect network %s: %w", id, err)
	}
	var inspected []struct {
		ID         string                     `json:"Id"`
		Name       string                     `json:"Name"`
		Labels     map[string]string          `json:"Labels"`
		Containers map[string]json.RawMessage `json:"Containers"`
	}
	if err := json.Unmarshal(output, &inspected); err != nil {
		return fmt.Errorf("decode network inspection: %w", err)
	}
	if len(inspected) != 1 {
		return errUnsafeNetwork
	}
	network := inspected[0]
	configured, managed := networks[network.Labels["com.docker.compose.network"]]
	if network.ID != id || network.Labels["com.docker.compose.project"] != opscontrol.ComposeProjectName() || !managed || configured.Name != network.Name || network.Containers == nil || len(network.Containers) != 0 {
		return errUnsafeNetwork
	}
	if _, _, err := c.networkRecoveryOutput([]string{"network", "rm", id}); err != nil {
		return fmt.Errorf("remove stale network %s: %w", id, err)
	}
	return nil
}

// Config output can contain interpolated secrets: capture bounded output, never log it.
func (c *Compose) networkRecoveryOutput(args []string) ([]byte, []byte, error) {
	stdout, stderr := diagnosticTail{limit: 4 << 20}, diagnosticTail{limit: 64 << 10}
	cmd := exec.Command(c.Runtime.Bin, args...)
	cmd.Env = c.composeEnv()
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	if stdout.truncated || stderr.truncated {
		return nil, nil, errNetworkDiagnostics
	}
	return stdout.bytes, stderr.bytes, err
}
