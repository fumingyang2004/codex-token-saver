// Replacement for pinned RTK 0.48.0 run_diff. Keep Git's exit status and both
// output streams, including code 1 (differences) outside a Git repository.
fn run_diff(
    args: &[String],
    max_lines: Option<usize>,
    verbose: u8,
    global_args: &[String],
) -> Result<i32> {
    let timer = tracking::TimedExecution::start();
    let args = &args_utils::restore_double_dash(args);
    let wants_stat = args.iter().any(|a| matches!(a.as_str(), "--stat" | "--numstat" | "--shortstat"));
    let compact = !args.iter().any(|a| a == "--no-compact") && !emits_word_diff(args);
    // --check reports whitespace errors; --quiet suppresses output deliberately.
    let diagnostic = args.iter().any(|a| matches!(a.as_str(), "--check" | "--quiet"));
    let observe = |result: &CaptureResult, shown: &str| {
        let raw = format!("{}{}", result.stdout, result.stderr);
        crate::core::ces_observer::register(&raw, "git.diff.actual-capture");
        crate::core::ces_observer::observe(&raw, shown);
    };
    let run = |stat: bool| -> Result<CaptureResult> {
        let mut cmd = git_cmd(global_args);
        cmd.arg("diff");
        if stat { cmd.arg("--stat"); }
        for arg in args {
            if arg != "--no-compact" { cmd.arg(arg); }
        }
        exec_capture(&mut cmd).context("Failed to run git diff")
    };
    if wants_stat || !compact || diagnostic {
        let result = run(false)?;
        print!("{}", result.stdout);
        eprint!("{}", result.stderr);
        observe(&result, &result.stdout);
        timer.track("git diff (passthrough)", "rtk git diff (passthrough)", &result.stdout, &result.stdout);
        return Ok(result.exit_code);
    }
    let usable = |r: &CaptureResult| r.exit_code == 0 || (r.exit_code == 1 &&
        r.stderr.lines().all(|l| l.trim().is_empty() || l.starts_with("warning:")));
    let stat = run(true)?;
    if !usable(&stat) {
        print!("{}", stat.stdout);
        eprint!("{}", stat.stderr);
        observe(&stat, &stat.stdout);
        return Ok(stat.exit_code);
    }
    if verbose > 0 { eprintln!("Git diff summary:"); }
    let result = run(false)?;
    if !usable(&result) {
        print!("{}", result.stdout);
        eprint!("{}", result.stderr);
        observe(&result, &result.stdout);
        return Ok(result.exit_code);
    }
    let printed = if result.stdout.is_empty() {
        stat.stdout.trim().to_string()
    } else {
        format!("{}\n\nChanges:\n{}", stat.stdout.trim(), compact_diff(&result.stdout, max_lines.unwrap_or(500)))
    };
    let raw = format!("{}\n{}", stat.stdout, result.stdout);
    let shown = never_worse(&raw, &printed);
    println!("{}", shown);
    eprint!("{}", result.stderr);
    observe(&result, shown);
    timer.track("git diff (compact)", "rtk git diff (compact)", &raw, shown);
    Ok(result.exit_code)
}
