package compose

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/runtime"
)

const (
	firstNetworkID  = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	secondNetworkID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
)

func TestMain(m *testing.M) {
	if os.Getenv("DECEPTICON_NETWORK_TEST_HELPER") == "1" {
		os.Exit(networkRecoveryProcess(os.Args[1:]))
	}
	os.Exit(m.Run())
}

// The test binary implements the Docker CLI boundary without contacting a daemon.
func networkRecoveryProcess(args []string) int {
	root := os.Getenv("DECEPTICON_NETWORK_TEST_DIR")
	log, err := os.OpenFile(filepath.Join(root, "calls"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return 90
	}
	if err := json.NewEncoder(log).Encode(args); err != nil {
		log.Close()
		return 91
	}
	if err := log.Close(); err != nil {
		return 92
	}
	scenario := os.Getenv("DECEPTICON_NETWORK_TEST_SCENARIO")
	if len(args) > 1 && args[0] == "network" {
		id := args[len(args)-1]
		if args[1] == "rm" {
			if scenario == "remove failure" {
				fmt.Fprintln(os.Stderr, "removal denied")
				return 1
			}
			fmt.Println(id)
			return 0
		}
		if args[1] != "inspect" {
			return 93
		}
		switch scenario {
		case "absent":
			fmt.Fprintf(os.Stderr, "Error response from daemon: network %s not found\n", id)
			return 1
		case "inspect denied with ID":
            fmt.Fprintf(os.Stderr,"permission denied: network %s not found\n",id)
            return 1
        case "inspect denied":
			fmt.Fprintln(os.Stderr, "permission denied")
			return 1
		case "malformed":
			fmt.Println("invalid json")
			return 0
		}
		project, logical, name, containers := "decepticon-fixture", "decepticon-net", "fixture-main", "{}"
		if id == secondNetworkID {
			logical, name = "sandbox-net", "fixture-sandbox"
		}
		switch scenario {
		case "foreign":
			project = "another-project"
		case "active":
			containers = `{"active":{"Name":"other-container"}}`
		case "unmanaged":
			logical = "unconfigured"
		case "wrong name":
			name = "another-network"
		}
		fmt.Printf(`[{"Id":%q,"Name":%q,"Labels":{"com.docker.compose.project":%q,"com.docker.compose.network":%q},"Containers":%s}]`+"\n", id, name, project, logical, containers)
		return 0
	}
	for _, arg := range args {
		if arg == "config" {
			external := scenario == "external"
			fmt.Printf(`{"networks":{"decepticon-net":{"name":"fixture-main","external":%t},"sandbox-net":{"name":"fixture-sandbox"}}}`+"\n", external)
			return 0
		}
		if arg == "up" {
			countPath := filepath.Join(root, "attempts")
			previous, err := os.ReadFile(countPath)
			if err != nil && !os.IsNotExist(err) {
				return 94
			}
			attempt := 0
			if len(previous) != 0 {
				attempt, err = strconv.Atoi(string(previous))
				if err != nil {
					return 98
				}
			}
			if err := os.WriteFile(countPath, []byte(strconv.Itoa(attempt+1)), 0o600); err != nil {
				return 95
			}
			if os.Getenv("DECEPTICON_WORKSPACE_HOST") != "fixture-workspace" {
				return 96
			}
			if scenario == "unrelated" {
				fmt.Fprintln(os.Stderr, "database healthcheck failed")
				return 1
			}
			if (scenario == "two" && attempt >= 2) || (scenario == "absent" && attempt >= 1) {
				return 0
			}
			id := firstNetworkID
			if scenario == "two" && attempt == 1 {
				id = secondNetworkID
			}
			if scenario == "budget" {
				id = strings.Repeat(strconv.Itoa(attempt+1), 64)
			}
			fmt.Fprintf(os.Stderr, "Error response from daemon: failed to set up container networking: network %s does not exist\n", id)
			return 1
		}
	}
	return 97
}

func TestUpRecoversOnlyMissingManagedDockerNetworks(t *testing.T) {
	for _, tc := range []struct {
		scenario  string
		runtime   string
		attempts  int
		removed   []string
		wantError bool
		databases bool
	}{
		{"two", "docker", 3, []string{firstNetworkID, secondNetworkID}, false, false},
		{"absent", "docker", 2, nil, false, false},
		{"foreign", "docker", 1, nil, true, false},
		{"active", "docker", 1, nil, true, false},
		{"external", "docker", 1, nil, true, false},
		{"unmanaged", "docker", 1, nil, true, false},
		{"wrong name", "docker", 1, nil, true, false},
		{"inspect denied", "docker", 1, nil, true, false},
        {"inspect denied with ID", "docker", 1, nil, true, false},
		{"malformed", "docker", 1, nil, true, false},
		{"remove failure", "docker", 1, []string{firstNetworkID}, true, false},
		{"repeated", "docker", 2, []string{firstNetworkID}, true, false},
		{"unrelated", "docker", 1, nil, true, false},
		{"two", "podman", 1, nil, true, false},
		{"two", "docker", 3, []string{firstNetworkID, secondNetworkID}, false, true},
		{"budget", "docker", 3, []string{strings.Repeat("1", 64), strings.Repeat("2", 64)}, true, false},
	} {
		t.Run(fmt.Sprintf("%s/%s/databases=%t", tc.scenario, tc.runtime, tc.databases), func(t *testing.T) {
			// Given an executable Docker fixture and the launcher's existing environment.
			root := t.TempDir()
			t.Setenv("DECEPTICON_NETWORK_TEST_HELPER", "1")
			t.Setenv("DECEPTICON_NETWORK_TEST_DIR", root)
			t.Setenv("DECEPTICON_NETWORK_TEST_SCENARIO", tc.scenario)
			t.Setenv("DECEPTICON_COMPOSE_PROJECT", "decepticon-fixture")
			t.Setenv("DECEPTICON_WORKSPACE_HOST", "fixture-workspace")
			t.Setenv("GORACE", "atexit_sleep_ms=0")
			c := &Compose{Home: root, ComposeFile: filepath.Join(root, "docker-compose.yml"), EnvFile: filepath.Join(root, ".env"), Runtime: runtime.Runtime{Name: tc.runtime, Bin: os.Args[0], ComposeArgs: []string{"compose"}}}
			// When startup encounters a missing endpoint network.
			var err error
			if tc.databases {
				err = c.UpDatabases()
			} else {
				err = c.Up("cli")
			}
			// Then only the implicated safe networks are removed and retries are bounded.
			if (err != nil) != tc.wantError {
				t.Fatalf("Up error = %v, wantError %v", err, tc.wantError)
			}
			raw, readErr := os.ReadFile(filepath.Join(root, "calls"))
			if readErr != nil {
				t.Fatal(readErr)
			}
			var removed []string
			attempts := 0
			for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
				var args []string
				if err := json.Unmarshal([]byte(line), &args); err != nil {
					t.Fatal(err)
				}
				if len(args) > 1 && args[0] == "network" && args[1] == "rm" {
					removed = append(removed, args[2])
				}
				for _, arg := range args {
					if arg == "up" {
						attempts++
						if attempts > 1 && !strings.Contains(strings.Join(args, " "), "--force-recreate") {
							t.Fatalf("retry did not recreate containers: %v", args)
						}
					}
					if arg == "down" || arg == "prune" || arg == "--volumes" {
						t.Fatalf("unsafe cleanup: %v", args)
					}
				}
			}
			if attempts != tc.attempts || !reflect.DeepEqual(removed, tc.removed) {
				t.Fatalf("attempts=%d removed=%v, want %d %v", attempts, removed, tc.attempts, tc.removed)
			}
		})
	}
}

func TestDiagnosticTailKeepsOnlyBoundedSuffix(t *testing.T) {
	for _, chunks := range [][]string{{"0123456789"}, {"012", "345", "6789"}} {
		tail := diagnosticTail{limit: 5}
		for _, chunk := range chunks {
			written, err := tail.Write([]byte(chunk))
			if written != len(chunk) || err != nil {
				t.Fatalf("write = %d, %v", written, err)
			}
		}
		if string(tail.bytes) != "56789" || !tail.truncated {
			t.Fatalf("captured %q, truncated=%t", tail.bytes, tail.truncated)
		}
	}
}
