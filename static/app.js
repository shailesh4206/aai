// Delta Trading Dashboard Frontend
class TradingDashboard {
    constructor() {
        this.statusInterval = null;
        this.tradesInterval = null;
        this.statsInterval = null;
        this.init();
    }

    init() {
        this.bindEvents();
        this.updateStatus();
        this.updateStats();
        this.updateTrades();
        this.startPolling();
    }

    bindEvents() {
        const startBtn = document.getElementById('start-btn');
        const stopBtn = document.getElementById('stop-btn');

        startBtn.addEventListener('click', () => this.startTrading());
        stopBtn.addEventListener('click', () => this.stopTrading());
    }

    async startTrading() {
        const startBtn = document.getElementById('start-btn');
        const stopBtn = document.getElementById('stop-btn');
        const symbolsSelect = document.getElementById('symbols');
        const intervalSelect = document.getElementById('interval');
        const balanceInput = document.getElementById('balance');

        const symbols = Array.from(symbolsSelect.selectedOptions).map(option => option.value);
        const interval = intervalSelect.value;
        const balance = parseFloat(balanceInput.value);

        if (symbols.length === 0) {
            this.showNotification('Please select at least one symbol', 'error');
            return;
        }

        startBtn.classList.add('loading');
        startBtn.disabled = true;

        try {
            const response = await fetch('/api/start', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    symbols: symbols,
                    interval: interval,
                    account_balance: balance
                })
            });

            const data = await response.json();

            if (data.ok) {
                this.showNotification('Trading started successfully', 'success');
                startBtn.disabled = true;
                stopBtn.disabled = false;
                this.updateStatus();
            } else {
                throw new Error('Failed to start trading');
            }
        } catch (error) {
            console.error('Error starting trading:', error);
            this.showNotification('Failed to start trading', 'error');
        } finally {
            startBtn.classList.remove('loading');
        }
    }

    async stopTrading() {
        const startBtn = document.getElementById('start-btn');
        const stopBtn = document.getElementById('stop-btn');

        stopBtn.classList.add('loading');
        stopBtn.disabled = true;

        try {
            const response = await fetch('/api/stop', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                }
            });

            const data = await response.json();

            if (data.ok) {
                this.showNotification('Trading stopped successfully', 'success');
                startBtn.disabled = false;
                stopBtn.disabled = true;
                this.updateStatus();
            } else {
                throw new Error('Failed to stop trading');
            }
        } catch (error) {
            console.error('Error stopping trading:', error);
            this.showNotification('Failed to stop trading', 'error');
        } finally {
            stopBtn.classList.remove('loading');
        }
    }

    async updateStatus() {
        try {
            const response = await fetch('/api/status');
            const data = await response.json();

            const statusIndicator = document.getElementById('status-indicator');
            const startBtn = document.getElementById('start-btn');
            const stopBtn = document.getElementById('stop-btn');

            if (data.running) {
                statusIndicator.classList.add('active');
                statusIndicator.querySelector('.status-text').textContent = 'Running';
                startBtn.disabled = true;
                stopBtn.disabled = false;
            } else {
                statusIndicator.classList.remove('active');
                statusIndicator.querySelector('.status-text').textContent = 'Stopped';
                startBtn.disabled = false;
                stopBtn.disabled = true;
            }

            // Update symbols and interval in UI
            const symbolsSelect = document.getElementById('symbols');
            const intervalSelect = document.getElementById('interval');

            // Update selected symbols
            Array.from(symbolsSelect.options).forEach(option => {
                option.selected = data.symbols.includes(option.value);
            });

            // Update interval
            intervalSelect.value = data.interval;

            // Update balance
            document.getElementById('active-balance').textContent = `₹${data.account_balance.toFixed(2)}`;

        } catch (error) {
            console.error('Error updating status:', error);
        }
    }

    async updateStats() {
        try {
            const response = await fetch('/api/stats');
            const data = await response.json();

            // Animate stat updates
            this.animateStatUpdate('total-pnl', `₹${data.total_pnl.toFixed(2)}`);
            this.animateStatUpdate('total-trades', data.total_trades);

            const winRate = data.total_trades > 0 ? ((data.wins / data.total_trades) * 100).toFixed(1) : 0;
            this.animateStatUpdate('win-rate', `${winRate}%`);

            // Color code P&L
            const pnlElement = document.getElementById('total-pnl');
            pnlElement.classList.remove('pnl-positive', 'pnl-negative');
            if (data.total_pnl > 0) {
                pnlElement.classList.add('pnl-positive');
            } else if (data.total_pnl < 0) {
                pnlElement.classList.add('pnl-negative');
            }

        } catch (error) {
            console.error('Error updating stats:', error);
        }
    }

    animateStatUpdate(elementId, newValue) {
        const element = document.getElementById(elementId);
        if (!element) return;

        const currentValue = element.textContent;
        if (currentValue === newValue) return;

        // Add animation class
        element.classList.add('stat-updating');

        // Update the value
        element.textContent = newValue;

        // Remove animation class after animation completes
        setTimeout(() => {
            element.classList.remove('stat-updating');
        }, 600);
    }

    async updateTrades() {
        try {
            const response = await fetch('/api/trades');
            const data = await response.json();

            const tbody = document.getElementById('trades-body');

            if (data.rows.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="no-data">No trades yet</td></tr>';
                return;
            }

            tbody.innerHTML = data.rows.map(trade => {
                const pnlClass = trade.pnl > 0 ? 'pnl-positive' : trade.pnl < 0 ? 'pnl-negative' : '';
                const pnlSign = trade.pnl > 0 ? '+' : '';
                return `
                    <tr>
                        <td>${new Date(trade.ts_utc).toLocaleString()}</td>
                        <td>${trade.symbol}</td>
                        <td>${trade.side.toUpperCase()}</td>
                        <td>$${trade.entry.toFixed(2)}</td>
                        <td>$${trade.exit.toFixed(2)}</td>
                        <td>${trade.qty.toFixed(6)}</td>
                        <td class="${pnlClass}">${pnlSign}$${trade.pnl.toFixed(2)}</td>
                    </tr>
                `;
            }).join('');

        } catch (error) {
            console.error('Error updating trades:', error);
        }
    }

    startPolling() {
        // Update status every 5 seconds
        this.statusInterval = setInterval(() => this.updateStatus(), 5000);

        // Update stats every 10 seconds
        this.statsInterval = setInterval(() => this.updateStats(), 10000);

        // Update trades every 15 seconds
        this.tradesInterval = setInterval(() => this.updateTrades(), 15000);
    }

    stopPolling() {
        if (this.statusInterval) clearInterval(this.statusInterval);
        if (this.statsInterval) clearInterval(this.statsInterval);
        if (this.tradesInterval) clearInterval(this.tradesInterval);
    }

    showNotification(message, type = 'info') {
        // Create notification element
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.textContent = message;

        // Add to page
        document.body.appendChild(notification);

        // Animate in
        setTimeout(() => notification.classList.add('show'), 10);

        // Remove after 3 seconds
        setTimeout(() => {
            notification.classList.remove('show');
            setTimeout(() => document.body.removeChild(notification), 300);
        }, 3000);
    }
}

// Notification styles (add to CSS if needed)
const notificationStyles = `
.notification {
    position: fixed;
    top: 20px;
    right: 20px;
    padding: 1rem 1.5rem;
    border-radius: 8px;
    color: white;
    font-weight: 500;
    z-index: 1000;
    transform: translateX(100%);
    transition: transform 0.3s ease;
    max-width: 300px;
}

.notification.show {
    transform: translateX(0);
}

.notification-success {
    background: #10b981;
}

.notification-error {
    background: #ef4444;
}

.notification-info {
    background: #3b82f6;
}
`;

// Add notification styles to head
const style = document.createElement('style');
style.textContent = notificationStyles;
document.head.appendChild(style);

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new TradingDashboard();
});
