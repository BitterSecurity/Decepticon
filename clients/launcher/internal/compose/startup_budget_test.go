package compose

import (
	"fmt"
	"os"
	"slices"
	"testing"

	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/runtime"
)

// Exercise the actual subprocess environment and arguments without starting
// services: both must agree even when the operator supplies an invalid value.
func TestStartupBudgetHelperProcess(t *testing.T) {
	if os.Getenv("DECEPTICON_TEST_STARTUP_HELPER") != "1" {
		return
	}
	args := os.Args[slices.Index(os.Args, "--")+1:]
	want := os.Getenv("DECEPTICON_TEST_STARTUP_EXPECTED")
	index := slices.Index(args, "--wait-timeout")
	if index < 0 || index+1 >= len(args) || args[index+1] != want || os.Getenv("DECEPTICON_STARTUP_TIMEOUT_SECONDS") != want {
		fmt.Fprintln(os.Stderr, "startup arguments and Compose interpolation budget disagree")
		os.Exit(2)
	}
	if !slices.Contains(args, "--wait") || !slices.Contains(args, "--no-build") {
		os.Exit(3)
	}
	os.Exit(0)
}

func TestUpForwardsValidatedStartupBudget(t *testing.T) {
	for _, operation := range []string{"all", "desktop", "databases"} {
		for _, input := range []string{"", "1200", "001200", "invalid", "0", "-1"} {
			t.Run(operation+"/"+input, func(t *testing.T) {
				want := "600"
				if input == "1200" || input == "001200" {
					want = "1200"
				}
				t.Setenv("DECEPTICON_TEST_STARTUP_HELPER", "1")
				t.Setenv("DECEPTICON_TEST_STARTUP_EXPECTED", want)
				t.Setenv("DECEPTICON_STARTUP_TIMEOUT_SECONDS", input)
				c := &Compose{Home: t.TempDir(), Runtime: runtime.Runtime{
					Name: "test", Bin: os.Args[0], ComposeArgs: []string{"-test.run=TestStartupBudgetHelperProcess", "--"},
				}}
				var err error
				switch operation {
				case "all":
					err = c.Up("cli")
				case "desktop":
					err = c.UpDesktop()
				case "databases":
					err = c.UpDatabases()
				}
				if err != nil {
					t.Fatal(err)
				}
			})
		}
	}
}
