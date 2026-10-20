package main

import (
	"context"
	"fmt"
	"os"

	"github.com/ccfos/huatuo/internal/storage/driver"
	"github.com/ccfos/huatuo/internal/storage/sqlite"
)

func main() {
	if len(os.Args) != 3 {
		panic("usage: sqlite-cap-repro query|values normal|max")
	}
	ctx := context.Background()
	backend, err := sqlite.NewBackend(":memory:")
	if err != nil {
		panic(err)
	}
	defer func() { _ = backend.Close(ctx) }()
	if err := backend.Init(ctx, "allocation_repro", nil); err != nil {
		panic(err)
	}
	bound := driver.DefaultBatchSize
	switch os.Args[2] {
	case "normal":
	case "max":
		bound = int(^uint(0) >> 1)
	default:
		panic("expected normal|max")
	}
	switch os.Args[1] {
	case "query":
		err = backend.Query(ctx, driver.Query{Limit: bound, BatchSize: bound}, func([]driver.Record) error { return nil })
	case "values":
		_, err = backend.Values(ctx, "kind", driver.Query{}, bound)
	default:
		panic("expected query|values")
	}
	if err != nil {
		panic(err)
	}
	fmt.Printf("%s %s: err=<nil> (empty table)\n", os.Args[1], os.Args[2])
}
