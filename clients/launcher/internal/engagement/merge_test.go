package engagement

import (
	"reflect"
	"testing"
)

func TestMergeRecommendations(t *testing.T) {
	cases := []struct {
		name                string
		remembered, live    []string
		want                []string
	}{
		{"remembered first then live", []string{"a", "b"}, []string{"b", "c"}, []string{"a", "b", "c"}},
		{"dedupe + trim + drop empty", []string{" a ", "a"}, []string{"", "a", "d"}, []string{"a", "d"}},
		{"only live", nil, []string{"x", "x", "y"}, []string{"x", "y"}},
		{"only remembered", []string{"m"}, nil, []string{"m"}},
		{"both empty", nil, nil, []string{}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := mergeRecommendations(tc.remembered, tc.live)
			if len(got) == 0 && len(tc.want) == 0 {
				return
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("mergeRecommendations(%v,%v) = %v, want %v", tc.remembered, tc.live, got, tc.want)
			}
		})
	}
}
