// diskpeek-scanner — fast directory walker + sizer
//
// Walks a directory tree and prints one line per file to stdout:
//   size\tpath\n
//
// Usage:
//   diskpeek-scanner [flags] [path]
//   -a    include hidden files and directories (dotfiles)
//
// Path defaults to the current directory.
// Symlinks are always skipped.
// Permission errors are silently skipped.

package main

import (
	"bufio"
	"flag"
	"fmt"
	"os"
	"runtime"
	"strings"
	"sync"
)

func main() {
	showHidden := flag.Bool("a", false, "include hidden files and directories")
	flag.Parse()

	root := "."
	if flag.NArg() > 0 {
		root = flag.Arg(0)
	}

	numWorkers := runtime.NumCPU() * 4
	if numWorkers > 32 {
		numWorkers = 32
	}

	// Pipeline: walker → pathCh → stat workers → resultCh → stdout
	pathCh := make(chan string, 1024)
	resultCh := make(chan [2]string, 1024) // [size_str, path]

	// Walker goroutine — manual stack, uses os.ReadDir for free d_type
	go func() {
		defer close(pathCh)
		stack := []string{root}
		for len(stack) > 0 {
			dir := stack[len(stack)-1]
			stack = stack[:len(stack)-1]
			entries, err := os.ReadDir(dir)
			if err != nil {
				continue
			}
			for _, e := range entries {
				if !*showHidden && strings.HasPrefix(e.Name(), ".") {
					continue
				}
				fullPath := dir + "/" + e.Name()
				typ := e.Type()
				if typ&os.ModeSymlink != 0 {
					continue // skip symlinks
				}
				if typ.IsDir() {
					stack = append(stack, fullPath)
				} else if typ.IsRegular() {
					pathCh <- fullPath
				}
			}
		}
	}()

	// Stat worker pool
	var wg sync.WaitGroup
	for i := 0; i < numWorkers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for p := range pathCh {
				info, err := os.Lstat(p)
				if err != nil {
					continue
				}
				resultCh <- [2]string{fmt.Sprintf("%d", info.Size()), p}
			}
		}()
	}

	// Close resultCh once all workers finish
	go func() {
		wg.Wait()
		close(resultCh)
	}()

	// Stream results to stdout with a large buffer to minimise syscalls.
	// The explicit Flush() before exit is critical — without it the last
	// partial buffer would be silently discarded.
	out := bufio.NewWriterSize(os.Stdout, 256*1024)
	for r := range resultCh {
		out.WriteString(r[0])
		out.WriteByte('\t')
		out.WriteString(r[1])
		out.WriteByte('\n')
	}
	out.Flush()
}
