from PySide2 import QtWidgets
import pyqtgraph as pg
import numpy as np
import xarray as xr
import sys

from HSTB.kluster.xarray_helpers import return_chunk_slices
from HSTB.kluster.fqpr_convenience import reload_data


class KlusterAttitudeView(pg.GraphicsLayoutWidget):
    """
    all addPlot does is

    plot = PlotItem(**kargs)
    self.addItem(plot, row, col, rowspan, colspan)
    return plot

    Look at superclassing PlotItem.  This would allow us to make attitude plot items and streamline this code.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setWindowTitle('Kluster Attitude View')

        self.pts_per_plot = 1000

        self.roll_plot = None
        self.pitch_plot = None
        self.heave_plot = None
        self.heading_plot = None

        self.active_curves = []

        self.data = None
        self.data_slices = None
        self.data_chunk_instances = []
        self.plot_data_instances = []
        self.plot_data_ids = []

        self.data_ptr = 0
        self.data_slice_index = 0
        self.plot_pts = 0

        self.timer = None

        self.build_plots()

    def initialize_datastore(self):
        """
        We hold the data for each chunk (and the next one is also buffered) so that it is available to the plot.  Clear
        out the datastore or build it for the first time here.

        """
        self.active_curves = []

        self.data = None
        self.data_slices = None
        self.data_chunk_instances = []
        self.plot_data_instances = []
        self.plot_data_ids = []

        self.data_ptr = 0
        self.data_slice_index = 0
        self.plot_pts = 0

        self.timer = None

    def build_plots(self):
        """
        Add the plots to KlusterAttitudeView with the settings contained in configure_attitude_plot

        """
        self.initialize_datastore()

        self.roll_plot = self.configure_attitude_plot(self.addPlot())
        self.roll_plot.setLabel('left', 'roll', 'deg')
        self.pitch_plot = self.configure_attitude_plot(self.addPlot())
        self.pitch_plot.setLabel('right', 'pitch', 'deg')
        self.nextRow()
        self.heave_plot = self.configure_attitude_plot(self.addPlot(colspan=2))
        self.heave_plot.setLabel('left', 'heave', 'm')
        self.nextRow()
        self.heading_plot = self.configure_attitude_plot(self.addPlot(colspan=2))
        self.heading_plot.setLabel('left', 'heading', 'deg')

    def configure_attitude_plot(self, newplot):
        """
        Basic config that applies to all the attitude plots.  We use autorange for expedience.  This might be annoying
        for some users, so we might want to set manual ranges in the future.

        Parameters
        ----------
        newplot: PlotItem object, created when we addPlot

        Returns
        -------
        configured PlotItem object

        """
        newplot.setDownsampling(ds=False)  # no downsampling
        newplot.setClipToView(False)
        newplot.enableAutoRange(x=True, y=True)
        # newplot.setRange(xRange=[0, self.pts_per_plot])
        # newplot.setLimits(xMax=self.pts_per_plot)
        return newplot

    def initialize_data(self, xarr):
        """
        Takes in an xarray object and builds the data indices.  data_chunk_instances will hold the computed dask
        array data.  We want to compute here because otherwise, each update is going to ask the dask array to compute
        for a single time, which adds overhead.  This frontloads that work.

        Parameters
        ----------
        xarr: xarray Dataset object, representing raw attitude as generated by Kluster.  Can be generated in other
              ways, see main statement for test dataset.

        """
        if xarr is not None:
            self.clear_data()
            self.data = xarr
            self.data_slices = return_chunk_slices(xarr)
            self.data_slice_index = 0
            self.data_ptr = 0
            self.data_chunk_instances.append(xarr.isel(time=self.data_slices[0]).compute())
            if len(self.data_slices) > 1:
                self.data_chunk_instances.append(xarr.isel(time=self.data_slices[1]).compute())
            if self.pts_per_plot > self.data_slices[0].stop:
                self.plot_pts = self.data_slices[0].stop
            else:
                self.plot_pts = self.pts_per_plot

            self.plot_data_instances = [np.empty((self.plot_pts, 2)), np.empty((self.plot_pts, 2)),
                                        np.empty((self.plot_pts, 2)), np.empty((self.plot_pts, 2))]
            self.plot_data_ids = ['roll', 'pitch', 'heave', 'heading']

    def start_plotting(self):
        """
        Kicks off the timer that runs the update.  I set the current refresh rate to an arbitrary 15ms.  This should
        probably be tied to the inherent logging rate of the xarray object feeding the plot.

        """
        self.timer = pg.QtCore.QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(15)

    def stop_plotting(self):
        """
        Stopping the timer will stop the updating.  When the widget is not visible, we stop the plotting
        """
        if self.timer is not None:
            if self.timer.isActive():
                self.timer.stop()

    def update_plot(self):
        """
        Data will come in chunked in some size that makes sense to the dask array (self.data_slices)
        We want chunks the length of the plot for display purposes (self.plot_pts) so we can show scrolling plots
        update_plot will:
         - build the lineplot objects and store them in active_curves
         - take the next dask array chunk, compute it, and hold it in data_chunk_instances (along with the current
                 dask chunk that was appended in initialize_data)
         - plot a chunk of that chunk, starting at index data_ptr and of length self.plot_pts
         - append a new value and remove the first value of that chunk every run of this method
         - re-intialize at the end of the array so that the plot starts over and runs indefinitely

        """
        slice_end = self.data_slices[self.data_slice_index].stop
        end_of_chunk = not self.data_ptr % slice_end
        if end_of_chunk:
            if self.data_ptr != 0:
                # next chunk
                self.clear_data()
                if self.data_ptr == self.data_slices[-1].stop:
                    # end of chunks
                    self.initialize_data(self.data)
                    return
                self.data_slice_index += 1
                self.data_chunk_instances.append(self.data.isel(time=self.data_slices[self.data_slice_index + 1]).compute())

            self.active_curves = [self.roll_plot.plot(), self.pitch_plot.plot(), self.heave_plot.plot(),
                                  self.heading_plot.plot()]

        if self.data_ptr == 0:
            curr_time_idx = slice(self.data_ptr, self.data_ptr + self.plot_pts)
            self.data_ptr += self.plot_pts
            raw_att = self.data_chunk_instances[0].isel(time=curr_time_idx)
            for cnt, curv in enumerate(self.active_curves):
                tmp = self.plot_data_instances[cnt]
                tmp[:, 0] = raw_att.time
                tmp[:, 1] = raw_att[self.plot_data_ids[cnt]]
                self.plot_data_instances[cnt] = tmp
                curv.setData(x=tmp[:, 0], y=tmp[:, 1])
        else:
            curr_time_idx = self.data_ptr - self.data_slices[self.data_slice_index].start
            self.data_ptr += 1
            raw_att = self.data_chunk_instances[0].isel(time=curr_time_idx)
            for cnt, curv in enumerate(self.active_curves):
                tmp = self.plot_data_instances[cnt]
                tmp[:-1] = self.plot_data_instances[cnt][1:]
                tmp[-1, 0] = float(raw_att.time)
                tmp[:-1, 1] = self.plot_data_instances[cnt][1:, 1]
                tmp[-1, 1] = float(raw_att[self.plot_data_ids[cnt]])
                self.plot_data_instances[cnt] = tmp
                curv.setData(x=tmp[:, 0], y=tmp[:, 1])

    def clear_data(self):
        """
        update_plot will continuously load one chunk of data at a time and plot the data to the four plots.  After
        a chunk is finished, we need to remove it from the list and clear the plots.

        If there is no data, we just continue on.

        """
        try:  # clear out any existing chunks
            self.data_chunk_instances = []
        except IndexError:
            pass

        try:  # remove any existing lines from the plot
            self.active_curves = []
            self.roll_plot.clear()
            self.pitch_plot.clear()
            self.heave_plot.clear()
            self.heading_plot.clear()
        except IndexError:
            pass


if __name__ == '__main__':
    app = QtWidgets.QApplication()
    test_window = KlusterAttitudeView()

    try:
        fq = reload_data(r"C:\collab\dasktest\data_dir\hassler_acceptance\refsurf\converted", show_progress=False)
        att_dat = fq.multibeam.raw_att
    except AttributeError:  # cant find the converted data, use this test data instead
        roll_dat = np.rad2deg(np.sin(np.linspace(-np.pi, np.pi, 2000)))
        pitch_dat = np.rad2deg(np.sin(np.linspace(np.pi, -np.pi, 2000)))
        heave_dat = np.linspace(0, 1, 2000)
        heading_dat = np.linspace(0, 180, 2000)
        time_dat = np.arange(0, 2000)
        att_dat = xr.Dataset({'roll': (['time'], roll_dat), 'pitch': (['time'], pitch_dat),
                              'heave': (['time'], heave_dat), 'heading': (['time'], heading_dat)},
                             coords={'time': time_dat}).chunk()
    test_window.build_plots()
    test_window.initialize_data(att_dat)
    test_window.start_plotting()
    test_window.show()
    sys.exit(app.exec_())
