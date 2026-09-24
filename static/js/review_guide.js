(() => {
    'use strict';

    const root = document.getElementById('reviewGuide');
    if (!root) return;

    const launcher = document.getElementById('reviewGuideLauncher');
    const panel = document.getElementById('reviewGuidePanel');
    const closeButton = document.getElementById('reviewGuideClose');
    const messages = document.getElementById('reviewGuideMessages');
    const form = document.getElementById('reviewGuideForm');
    const input = document.getElementById('reviewGuideInput');
    let guideContext = null;

    const roleFlows = {
        HR: 'Create a review cycle, assign employees with active blueprints, then activate it. Monitor progress and records. After review approval, the supervisor records the PAR outcome and any required development plan. HR can close the cycle when the required records are complete.',
        Supervisor: 'Maintain your team’s performance blueprints. After the employee and peer reviews are submitted, complete your evaluation. Once the final outcome is acknowledged, arrange the PAR meeting, record its outcome, and create a PDP if one is required.',
        Manager: 'Your dashboard shows approval requests assigned to you. Review the evidence, then approve with a reason or send a private change request to selected contributors. Your availability can be included when the supervisor arranges a PAR meeting.',
        Employee: 'Complete and submit your self-assessment when it appears in your action stream. You may also receive a confidential peer review task. After approval, acknowledge your final outcome and update any PDP activities assigned to you.',
    };

    const pageHelp = {
        self_assessment_studio: 'Rate each locked performance item and explain your work with specific examples. Complete the overall summary, achievements, challenges and support needs. Save a draft while you work, then submit when every required part is complete.',
        peer_review_studio: 'Rate only work you have directly observed. Add a specific example for each item, then complete the overall feedback fields. Your name is hidden from the employee review workspace.',
        supervisor_evaluation_workspace: 'Compare the locked baseline, employee reflection and confidential peer evidence. Record a rating and rationale for every item, then complete your overall decision and submit it.',
        manager_approval_workspace: 'Check the complete evidence package. To approve, write an evidence-based approval note. To request changes, select each intended contributor and write a separate private note for that person.',
        par_meeting_workspace: 'The supervisor checks shared availability, schedules the meeting and invites the selected attendees. After the conversation, mark it held and record the discussion, agreed actions and PDP decision.',
        pdp_workspace: 'The supervisor creates the plan with a goal, a way to measure success, a target date and activities. The employee can update each activity’s status and progress note.',
        review_cycle_workspace: 'Assign eligible employees while the cycle is in preparation. Activate the cycle when assignments and baselines are ready, then monitor reviews and close it after the required outcomes are complete.',
        availability: 'Block times when you cannot attend. The scheduler uses these periods to avoid meeting conflicts; other attendees see available slots rather than your private reason.',
    };

    function safeLink(link) {
        return link && typeof link.url === 'string' && link.url.startsWith('/') && !link.url.startsWith('//');
    }

    function addMessage(speaker, content, link) {
        const bubble = document.createElement('div');
        bubble.className = `review-guide-message ${speaker}`;
        if (content.title) {
            const title = document.createElement('strong');
            title.textContent = content.title;
            bubble.append(title);
        }
        const body = document.createElement('span');
        body.textContent = content.text;
        bubble.append(body);
        if (safeLink(link)) {
            const anchor = document.createElement('a');
            anchor.className = 'review-guide-link';
            anchor.href = link.url;
            anchor.textContent = `${link.label || 'Open workspace'} →`;
            bubble.append(anchor);
        }
        messages.append(bubble);
        messages.scrollTop = messages.scrollHeight;
    }

    function shortcut(label) {
        return guideContext?.shortcuts?.find(item => item.label === label);
    }

    function answer(topic) {
        const role = guideContext?.role;
        const next = guideContext?.next_step;
        if (topic === 'next') {
            addMessage('guide', {
                title: next?.title || 'Your next step',
                text: next?.description || 'Open your dashboard to see your current actions.',
            }, next);
            return;
        }
        if (topic === 'workflow') {
            addMessage('guide', {title: 'Your review journey', text: roleFlows[role] || roleFlows.Employee},
                shortcut(role === 'HR' ? 'Review cycles' : role === 'Supervisor' ? 'My team' : 'Dashboard'));
            return;
        }
        if (topic === 'privacy') {
            addMessage('guide', {
                title: 'Who can see what',
                text: 'Peer reviewer names are hidden from the employee review workspace. A manager’s private change request is sent only to the people selected for that request, and each recipient sees only their own note. Other review pages follow your account’s role permissions.',
            });
            return;
        }
        if (topic === 'writing') {
            const advice = {
                HR: 'Write baselines that describe an observable responsibility, expectation, KPI or goal. Use a measurable result and a realistic target date where appropriate.',
                Supervisor: 'For each rating, describe the observed result, its impact and the evidence behind your judgment. Use the employee reflection and peer feedback, then make the development action specific.',
                Manager: 'State the evidence that supports approval. For a change request, choose the correct contributor and say exactly what detail or correction you need in that person’s private note.',
                Employee: 'For each item, describe what you did, what changed and what evidence supports your rating. Include an honest challenge and the support that would help you improve.',
            };
            addMessage('guide', {title: 'Writing useful feedback', text: advice[role] || 'Describe what you observed, give a specific example, explain its effect and suggest one practical next step.'});
            return;
        }
        if (topic === 'meeting') {
            const text = role === 'Supervisor'
                ? 'After the approved outcome is acknowledged, open the PAR workspace. Choose a weekday slot shown as available for the employee and supervisor; include the manager if needed. After the conversation, mark the meeting held and record its outcome.'
                : 'The employee and supervisor attend the PAR meeting. The manager may be included. Add your unavailable time so the supervisor can choose a shared free slot. Your reason for being unavailable is not shared with other attendees.';
            addMessage('guide', {title: 'PAR meeting', text}, shortcut('Availability'));
            return;
        }
        if (topic === 'development') {
            const text = role === 'Supervisor'
                ? 'When the PAR outcome requires a PDP, create a clear goal, success measure, target date and practical activities. Review the progress notes the employee adds later.'
                : role === 'Employee'
                    ? 'Open your development plan, update each activity’s status and add a concise progress note. Your supervisor receives an update when you save.'
                    : 'A PDP follows a PAR outcome when development actions are needed. The supervisor creates it, the employee updates activities, and HR monitors progress.';
            addMessage('guide', {title: 'Personal Development Plan', text}, shortcut('Development'));
            return;
        }
        if (topic === 'page' && pageHelp[root.dataset.page]) {
            addMessage('guide', {title: 'On this page', text: pageHelp[root.dataset.page]});
            return;
        }
        addMessage('guide', {
            title: 'I can help with that area',
            text: 'Try asking about your next step, the review stages, privacy, writing feedback, PAR meetings or development plans. The suggested questions below are a good place to start.',
        });
    }

    function topicFor(question) {
        const text = question.toLowerCase();
        if (/\b(next|pending|my task|my action|what do i do|what should i do)\b/.test(text)) return 'next';
        if (/\b(privacy|private|anonymous|confidential|who can see|who sees|identity|visible|change request)\b/.test(text)) return 'privacy';
        if (/\b(par|meeting|available|availability|schedule|attend)\b/.test(text)) return 'meeting';
        if (/\b(pdp|development plan|progress|activity|activities)\b/.test(text)) return 'development';
        if (/\b(this page|this form|this screen|here)\b/.test(text)) return 'page';
        if (/\b(write|word|feedback|rating|score|evidence|reflection|assess)\b/.test(text)) return 'writing';
        if (/\b(workflow|cycle|stage|step|process|review work)\b/.test(text)) return 'workflow';
        return 'unknown';
    }

    async function refreshContext() {
        messages.replaceChildren();
        addMessage('guide', {text: 'Checking your current review actions…'});
        try {
            const response = await fetch('/review-guide/context', {credentials: 'same-origin'});
            if (!response.ok) throw new Error('Guide context unavailable');
            guideContext = await response.json();
            messages.replaceChildren();
            addMessage('guide', {
                text: `Hi ${guideContext.first_name}. I can explain the review process and help you find your next step.`,
            });
            answer('next');
        } catch {
            guideContext = null;
            messages.replaceChildren();
            addMessage('guide', {
                text: 'I could not check your current actions. Refresh the page or open your dashboard and try again.',
            }, {label: 'Open dashboard', url: '/dashboard'});
        }
    }

    function setOpen(open) {
        panel.hidden = !open;
        launcher.setAttribute('aria-expanded', String(open));
        launcher.setAttribute('aria-label', open ? 'Close Review Guide' : 'Open Review Guide');
        if (open) {
            refreshContext();
            input.focus();
        } else {
            launcher.focus();
        }
    }

    launcher.addEventListener('click', () => setOpen(panel.hidden));
    closeButton.addEventListener('click', () => setOpen(false));
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !panel.hidden) setOpen(false);
    });
    document.addEventListener('pointerdown', event => {
        if (!panel.hidden && !root.contains(event.target)) setOpen(false);
    });
    root.querySelectorAll('[data-guide-topic]').forEach(button => {
        button.addEventListener('click', () => {
            addMessage('person', {text: button.textContent.trim()});
            answer(button.dataset.guideTopic);
        });
    });
    form.addEventListener('submit', event => {
        event.preventDefault();
        const question = input.value.trim();
        if (!question) return;
        addMessage('person', {text: question});
        answer(topicFor(question));
        input.value = '';
        input.focus();
    });
})();
