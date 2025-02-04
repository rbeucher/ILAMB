from .Confrontation import Confrontation,create_data_header
from scipy.interpolate import CubicSpline
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from .Variable import Variable
from .Regions import Regions
from .constants import mid_months,lbl_months,bnd_months
from . import Post as post
from . import ilamblib as il
from netCDF4 import Dataset
import pylab as plt
import numpy as np
import os
import copy
from . import ccgfilt



def _phaseWellDefined(t,v):
    """The phase of a site is considered well defined if:

    * there is at least 2 years of contiguous data in the time series
    * if the frequency corresponding to the peak in the power spectrum
    is within the sampling frequency of 1/365.

    """
    b,e = 0,0
    for s in np.ma.flatnotmasked_contiguous(v):
        if (s.stop-s.start) > e-b:
            b = s.start
            e = s.stop
    if (e-b) < 24: return False
    T  = t     [b:e]
    V  = v.data[b:e]
    P  = np.abs(np.fft.fft(V))**2
    dt = np.diff(T).mean()
    F  = np.fft.fftfreq(V.size,dt)
    P  = P[np.where(F>=0)]
    F  = F[np.where(F>=0)]
    i  = np.argsort(F)
    P  = P[i]; F = F[i]
    dF = np.diff(F).mean()
    f0 = 1./365
    f  = F[P.argmax()]
    return (f > (f0-0.9*dF))*(f < (f0+0.9*dF))

def _meanDay(d):
    """Computes the average Julian day by the angle of the resultant vector.
    """
    x  = (np.cos(d/365.*2*np.pi)).mean()
    y  = (np.sin(d/365.*2*np.pi)).mean()
    a  = np.arctan(y/x)/(2.*np.pi)*365.
    a += (x<=0)*365*0.5
    a += (a<=0)*365
    return a

def _cycleShape(var,period=365.):
    """Reshape the variable data for computing a cycle on the specified period.
    """
    dt    = (var.time_bnds[:,1]-var.time_bnds[:,0]).mean()
    spd   = int(round(period/dt))
    begin = np.argmin(var.time[:spd]%period)
    end   = begin+int(var.time[begin:].size/float(spd))*spd
    shp   = (-1,spd) + var.data.shape[1:]
    cycle = var.data[begin:end].reshape(shp)
    tbnd  = var.time_bnds[begin:end,:].reshape((-1,spd,2)) % period
    tbnd  = tbnd[0,...]
    tbnd[-1,1] = period
    t     = tbnd.mean(axis=1)
    return cycle,t,tbnd

def _siteCharacteristics(t,v):
    """Compute the mean amplitude, cycle, and time of maximum and minimum.
    """
    with np.errstate(under='ignore'):
        amp   = (v.max (axis=1)-v.min(axis=1)).mean()
        cyc   =  v.mean(axis=0)
        fun   = CubicSpline(np.hstack([t  ,t  [0]+365.]),
                            np.hstack([cyc,cyc[0]     ]),
                            bc_type="periodic")
        troot = fun.derivative().solve()
        troot = troot[(troot>=0)*(troot<=365.)]
        tmax  = troot[fun(troot).argmax()]
        tmin  = troot[fun(troot).argmin()]
        vs    = fun(np.linspace(0,365,366))
    return amp,tmax,tmin,vs

def _detrend(var):
    """Detrend the variable by subtracting the best fit quadratic polynomial.
    """
    for i in range(var.ndata):
        j = np.where(var.data[:,i].mask==False)
        t = var.time[j]
        x = var.data[j,i][0]
        p = np.polyfit(t,x,2)
        var.data.data[:,i] -= np.polyval(p,var.time)
    return var

def _computeShift(x,y):
    """Given the timing of two variables, compute a shift score
    """
    shift  = np.abs(x.data-y.data) # how many days off are we
    shift  = (shift>0.5*365)*(365-shift) + (shift<=0.5*365)*shift
    shift /= (0.5*365)
    shift  = 1.0-shift
    return shift

class ConfCO2(Confrontation):
    """
    """
    def __init__(self,**keywords):

        # Ugly, but this is how we call the Confrontation constructor
        super(ConfCO2,self).__init__(**keywords)
        self.regions = ['global']
        self.derived = self.keywords.get("emulated_flux","nbp")

        self.lat_bands = np.asarray(self.keywords.get("lat_bands","-90,-60,-23,0,+23,+60,+90").split(","),dtype=float)
        sec = []
        for i in range(len(self.lat_bands)-1):
            sec.append("Latitude Band %d to %d [ppm]" % (self.lat_bands[i],self.lat_bands[i+1]))
        sec = sec[::-1]

        # Setup a html layout for generating web views of the results
        pages = []

        # Mean State page
        pages.append(post.HtmlPage("MeanState","Mean State"))
        pages[-1].setHeader("CNAME / RNAME / MNAME")
        pages[-1].setSections(["Summary",] + sec)
        pages.append(post.HtmlAllModelsPage("AllModels","All Models"))
        pages[-1].setHeader("CNAME / RNAME")
        pages[-1].setSections([])
        pages[-1].setRegions(self.regions)
        pages.append(post.HtmlPage("DataInformation","Data Information"))
        pages[-1].setSections([])
        pages[-1].text = "\n"

        def _attribute_sort(attr):
            # If the attribute begins with one of the ones we
            # specifically order, return the index into order. If
            # it does not, return the number of entries in the
            # list and the file's order will be preserved.
            order = ['title','version','institution','source','history','references','comments','convention']
            for i,a in enumerate(order):
                if attr.lower().startswith(a): return i
            return len(order)
        with Dataset(self.source) as dset:
            attrs = dset.ncattrs()
            attrs = sorted(attrs,key=_attribute_sort)
            for attr in attrs:
                try:
                    val = dset.getncattr(attr)
                    if type(val) != str: val = str(val)
                    pages[-1].text += create_data_header(attr,val)
                except:
                    pass

        self.layout = post.HtmlLayout(pages,self.longname)

        # Adding a member variable called basins, add them as regions
        r = Regions()
        self.pulse_dir = "/".join(self.source.split("/")[:-2]+["PulseEmulation"])
        self.pulse_regions = r.addRegionNetCDF4(os.path.join(self.pulse_dir,"AtmosphericPulseRegions.nc"))

        # Emulation specific initialization
        self.sites = [site.strip() for site in self.keywords.get("sites",None).upper().split(",")]
        self.map   = None
        if self.sites:
            self.map  = [self.lbls.index(site) for site in self.sites if site in self.lbls]
            self.lbls = [self.lbls[i] for i in self.map]

    def emulatedModelResult(self,m,obs):

        # Emulation parameters
        emulated_flux = self.keywords.get("emulated_flux","nbp")
        spinup = 12
        Ninf   = 60
        ilev   = 1

        # Get the model result
        mod = m.extractTimeSeries(emulated_flux,
                                  initial_time = obs.time_bnds[ 0,0]-float(Ninf)/12*365+29.,
                                  final_time   = obs.time_bnds[-1,1])


        # What if I don't have Ninf leadtime?
        tf = min(obs.time_bnds[-1,1],mod.time_bnds[-1,1])
        if (tf % 365 > 2): tf -= (tf % 365) # needs to end in integer years
        obs.trim(t=[-1e20,tf])
        mod.trim(t=[-1e20,tf])

        # Integrate the emulated flux over each pulse region
        region_int = {}
        for region in self.pulse_regions: region_int[region] = mod.integrateInSpace(region=region).convert("Pg yr-1")

        # Load the operator from the files
        lat,lon,H = None,None,None
        for i in range(12):
            # FIX: move pulses into one file to avoid requiring a naming convention
            with Dataset(os.path.join(self.pulse_dir,"Pulse%02d.nc" % (i+1))) as dset:
                if lat is None: lat = dset.variables["lat"][...]
                if lon is None: lon = dset.variables["lon"][...]
                if H   is None: H   = np.zeros((22,12,Ninf+12,lat.size,lon.size))
                for j in range(22):
                    T = dset.variables['T%d' % (j+1)]
                    H[j,i,...] = T[spinup:,ilev,...]-T[:spinup,...].mean()

        # Where are our sites?
        ilat = np.abs(lat[:,np.newaxis]-obs.lat).argmin(axis=0)
        ilon = np.abs(lon[:,np.newaxis]-obs.lon).argmin(axis=0)

        # Apply the operator
        Nyrs  = int(mod.time.size/12)
        Ntot  = 12*Nyrs + Ninf
        eflux = np.zeros((obs.ndata,22,Ntot))
        for j in range(20):
            for s in range(obs.ndata):
                Htemp = H[j,...,ilat[s],ilon[s]]
                Htrac = np.zeros((22,Ntot,12*Nyrs))
                for i in range(Nyrs):
                    pb = 12*i
                    pe = 12*(i+1)
                    re = pe + Ninf
                    Htrac[j,pb:re,pb:pe] = Htemp.T
                    Htrac[j,re:  ,pb:pe] = np.tile(Htemp[:,-1],[12*(Nyrs-i-1),1])
                eflux[s,j,:] = np.dot(Htrac[j,...],region_int["pulse_region_%d" % (j+1)].data)*(-1e-3) # H is [] ?

        eflux = eflux.sum(axis=1).T
        eflux = eflux[Ninf:-Ninf]
        eflux = np.ma.masked_array(eflux,mask=obs.data.mask)
        mod = Variable(name      = "co2",
                       unit      = obs.unit,
                       lat       = obs.lat,
                       lon       = obs.lon,
                       ndata     = obs.ndata,
                       time      = obs.time,
                       time_bnds = obs.time_bnds,
                       data      = eflux)
        return mod

    def stageData(self,m):

        # Get the observational data
        obs = Variable(filename       = self.source,
                       variable_name  = self.variable,
                       alternate_vars = self.alternate_vars,
                       t0 = None if len(self.study_limits) != 2 else self.study_limits[0],
                       tf = None if len(self.study_limits) != 2 else self.study_limits[1])

        # Reduce the sites
        if self.map:
            obs.lat   = obs.lat  [  self.map]
            obs.lon   = obs.lon  [  self.map]
            obs.depth = obs.depth[  self.map]
            obs.data  = obs.data [:,self.map]
            obs.ndata = len(self.map)

        # Get the model result
        force_emulation = self.keywords.get("force_emulation","False").lower() == "true"
        never_emulation = self.keywords.get("never_emulation","False").lower() == "true"
        no_co2          = False
        emulated_co2    = False
        mod             = None


        if not force_emulation:
            try:
                mod = m.extractTimeSeries(self.variable,
                                          alt_vars     = self.alternate_vars,
                                          initial_time = obs.time_bnds[ 0,0],
                                          final_time   = obs.time_bnds[-1,1],
                                          lats         = None if obs.spatial else obs.lat,
                                          lons         = None if obs.spatial else obs.lon)


            except il.VarNotInModel:
                no_co2 = True

        if (((mod is None) or no_co2) and (not never_emulation)):
            mod = self.emulatedModelResult(m,obs)
            emulated_co2 = True

        if mod is None: raise il.VarNotInModel()

        # Get the right layering, closest to the layer elevation where all aren't masked.
        if mod.layered:
            ind = (np.abs(obs.depth[:,np.newaxis]-mod.depth)).argmin(axis=1)
            for i in range(ind.size):
                while (mod.data[:,ind[i],i].mask.sum() > 0.5*mod.data.shape[0]):
                    ind[i] += 1
            data = []
            for i in range(ind.size):
                data.append(mod.data[:,ind[i],i])
            mod.data = np.ma.masked_array(data).T
            mod.depth = None
            mod.depth_bnds = None
            mod.layered = False

            obs,mod = il.MakeComparable(obs,mod,
                                        mask_ref  = True,
                                        clip_ref  = True)
            mod.data.mask += obs.data.mask



        # if emulated_co2 is true, subtract mod by TakahashiFFco2 and FFco2
        if emulated_co2:
           #Read in Fosil fuel CO2 concentration from GEOSChem output
           filename = os.path.join(self.pulse_dir,"GEOSChemOcnFfCo2_32yr_360daytime.nc")

           FFco2Emu = Variable(filename = filename, variable_name = "FFco2" )
           FFco2Emu = FFco2Emu.extractDatasites(lat = None if obs.spatial else obs.lat,
                                                lon = None if obs.spatial else obs.lon )
           OCNco2Emu = Variable(filename = filename, variable_name = "OCNco2" )
           OCNco2Emu = OCNco2Emu.extractDatasites(lat = None if obs.spatial else obs.lat,
                                                  lon = None if obs.spatial else obs.lon)

           # Get the right layering, closest to the layer elevation where all aren't masked
           if OCNco2Emu.layered:
              ind = (np.abs(obs.depth[:,np.newaxis]-OCNco2Emu.depth)).argmin(axis=1)
              for i in range(ind.size):
                  while (OCNco2Emu.data[:,ind[i],i].mask.sum() > 0.5*OCNco2Emu.data.shape[0]):
                      ind[i] += 1

              data = []
              dataFF = []
              for i in range(ind.size):
                  data.append(OCNco2Emu.data[:,ind[i],i])
                  dataFF.append(FFco2Emu.data[:,ind[i],i])

              OCNco2Emu.data = np.ma.masked_array(data).T
              OCNco2Emu.depth = None
              OCNco2Emu.depth_bnds = None
              OCNco2Emu.layered = False
              OCNco2Emu.unit = "mol mol-1"

              FFco2Emu.data = np.ma.masked_array(dataFF).T
              FFco2Emu.depth = None
              FFco2Emu.depth_bnds = None
              FFco2Emu.layered = False
              FFco2Emu.unit = "mol mol-1"


           # actual processing substract OCNco2 and FFco2 from mod CO2
           obs, OCNco2Emu = il.MakeComparable(obs, OCNco2Emu,
                                              mask_ref = True,
                                              clip_ref = True)

           obs, FFco2Emu = il.MakeComparable(obs, FFco2Emu,
                                             mask_ref = True,
                                             clip_ref = True)

           #trim data in time domain
           tmin = max(OCNco2Emu.time_bnds[0,0],obs.time_bnds[0,0])
           tmax = min(OCNco2Emu.time_bnds[-1,1],obs.time_bnds[-1,1])

           if tmax >= tmin:
               OCNco2Emu.trim(t=[tmin, tmax])
               FFco2Emu.trim(t=[tmin, tmax])
               obs.trim(t=[tmin, tmax])
               obs.data = obs.data - OCNco2Emu.data - FFco2Emu.data
               mod.trim(t=[tmin, tmax])


        # Remove the trend via quadradic polynomial
        obs = _detrend(obs)
        mod = _detrend(mod)


        return obs,mod

    def relationshipInd(self,m):
        #Before plotting relationship between iav of co2 or co2 growth rate and iav of other variables, e.g. tas in this case, prepare the iav of independent variable at the same time.

        #get obs of independent variable
        indObs = Variable(filename       = os.path.join(self.pulse_dir, "/DATA/tas/CRU/tas_0.5x0.5.nc"),
                          variable_name  = "tas",
                          #alternate_vars = self.alternate_vars,
                          t0 = None ,
                          tf = None)

        #grab tropical area
        latTro = indObs.lat[(indObs.lat > -23) * (indObs.lat < 23)]
        maskLat = np.repeat(latTro, len(indObs.lon))
        maskLon = np.tile(indObs.lon, len(latTro))
        indObs =  indObs.extractDatasites(lat = maskLat,
                                          lon = maskLon)

        #grab tropical land area
        whrLand = np.where(indObs.data[1,:].mask==False)
        indObsLand = copy.deepcopy(indObs)
        indObsLand.data = indObs.data[:, whrLand]


        #detrending for indObsLand
        cc = copy.deepcopy(indObsLand)
        cc.data = np.mean(indObsLand.data, axis=2, keepdims = False)
        cc.ndata = 1
        var = copy.deepcopy(cc)
        j = np.where(var.data.mask==False)[0]
        t = var.time[j]
        x = var.data[j,0]
        p = np.polyfit(t,x,2)
        bb = np.polyval(p,var.time)
        var.data[j,0] -= bb

        indObsLandDtr = copy.deepcopy(var)



        #-------model output tas ---------------------

        #get reference co2 obs to grab the time
        co2Obs = Variable(filename       = self.source,
                       variable_name  = self.variable,
                       alternate_vars = self.alternate_vars,
                       t0 = None if len(self.study_limits) != 2 else self.study_limits[0],
                       tf = None if len(self.study_limits) != 2 else self.study_limits[1])


        #get the model output of independent variable
        indModLand = m.extractTimeSeries("tas",
                                         #alt_vars     = self.alternate_vars,
                                         initial_time = co2Obs.time_bnds[ 0,0],
                                         final_time   = co2Obs.time_bnds[-1,1],
                                         lats         = indObs.lat[whrLand],
                                         lons         = indObs.lon[whrLand])


        #detrending for mod independent-----------
        cc = copy.deepcopy(indModLand)
        cc.data = np.mean(indModLand.data, axis=1, keepdims= True)
        cc.ndata = 1
        var = copy.deepcopy(cc)
        j = np.where(var.data.mask==False)[0]
        t = var.time[j]
        x = var.data[j,0]
        p = np.polyfit(t,x,2)
        #var.data.data[:,i] -= np.polyval(p,var.time)
        bb = np.polyval(p,var.time)
        var.data[j,0] -= bb
        indModLandDtr = copy.deepcopy(var)




        ###calcualte iav of ind obs and ind mod----------

        # Compute harmonics first
        ocyc,ot,otb = _cycleShape(indObsLandDtr)
        mcyc,mt,mtb = _cycleShape(indModLandDtr)

        n           = 1
        obs_amp     = np.zeros(n); obs_maxp = np.zeros(n); obs_minp = np.zeros(n)
        mod_amp     = np.zeros(n); mod_maxp = np.zeros(n); mod_minp = np.zeros(n)
        obs_cyc     = np.zeros((366,n)); mod_cyc = np.zeros((366,n));
        well_define = np.zeros(n)


        for i in range(1,n):
            obs_amp[i],obs_maxp[i],obs_minp[i],obs_cyc[:,i] = _siteCharacteristics(ot,ocyc[...,i])
            mod_amp[i],mod_maxp[i],mod_minp[i],mod_cyc[:,i] = _siteCharacteristics(mt,mcyc[...,i])

        obs = copy.deepcopy(indObsLandDtr)
        mod = copy.deepcopy(indModLandDtr)


        with np.errstate(under='ignore'):
            ocyc     = Variable(name  = "cycle", # mean annual cycle
                                unit  = obs.unit,
                                data  = ocyc.mean(axis=0),
                                ndata = obs.ndata,
                                lat   = obs.lat,
                                lon   = obs.lon,
                                time  = ot,
                                time_bnds = otb)

            oiav     = Variable(name  = "iav", # deseasonalized interannual variability
                                unit  = obs.unit,
                                data  = obs.data-il.ExtendAnnualCycle(obs.time,ocyc.data,ocyc.time),
                                time  = obs.time,
                                ndata = obs.ndata,
                                lat   = obs.lat,
                                lon   = obs.lon,
                                time_bnds = obs.time_bnds)


        # Write out ILAMB variables for modeled quantities
        mcyc     = Variable(name  = "cycle", # mean annual cycle
                            unit  = mod.unit,
                            data  = mcyc.mean(axis=0),
                            ndata = mod.ndata,
                            lat   = mod.lat,
                            lon   = mod.lon,
                            time  = mt,
                            time_bnds = mtb)
        miav     = Variable(name  = "iav", # deseasonalized interannual variability
                            unit  = mod.unit,
                            data  = mod.data-il.ExtendAnnualCycle(mod.time,mcyc.data,mcyc.time),
                            time  = mod.time,
                            ndata = mod.ndata,
                            lat   = mod.lat,
                            lon   = mod.lon,
                            time_bnds = mod.time_bnds)




        # Write out the intermediate variables
        #write out independent variable, here is tas iav
        flag_write = False
        if flag_write:
            if not os.path.exists((os.path.join(self.output_path, "tas/"))):
                os.mkdir((os.path.join(self.output_path, "tas/")))
            with Dataset(os.path.join(self.output_path,"tas/%s_%s.nc" % ("tas",m.name)),mode="w") as results:
                results.setncatts({"name" :m.name, "color":m.color})
                for v in [mod,mcyc,miav]:
                    v.toNetCDF4(results,group="MeanState")

            with Dataset(os.path.join(self.output_path,"tas/tas_CRU_Benchmark.nc"),mode="w") as results:
                results.setncatts({"name" :"Benchmark", "color":np.asarray([0.5,0.5,0.5])})
                for v in [obs,ocyc,oiav]:
                    v.toNetCDF4(results,group="MeanState")

        return oiav,miav


    def confront(self,m):


        # Grab the data
        obs,mod= self.stageData(m)

        # Compute amplitude, min and max phase, and annual cycle as numpy data arrays
        ocyc,ot,otb = _cycleShape(obs)
        mcyc,mt,mtb = _cycleShape(mod)
        n           = len(self.lbls)
        obs_amp     = np.zeros(n); obs_maxp = np.zeros(n); obs_minp = np.zeros(n)
        mod_amp     = np.zeros(n); mod_maxp = np.zeros(n); mod_minp = np.zeros(n)
        obs_cyc     = np.zeros((366,n)); mod_cyc = np.zeros((366,n));
        well_define = np.zeros(n)
        for i,site in enumerate(self.lbls):
            obs_amp[i],obs_maxp[i],obs_minp[i],obs_cyc[:,i] = _siteCharacteristics(ot,ocyc[...,i])
            mod_amp[i],mod_maxp[i],mod_minp[i],mod_cyc[:,i] = _siteCharacteristics(mt,mcyc[...,i])
            well_define[i] = _phaseWellDefined(obs.time,obs.data[:,i])
        well_define /= well_define.sum()

        # Write out ILAMB variables for observed quantities
        with np.errstate(under='ignore'):
            ocyc     = Variable(name  = "cycle", # mean annual cycle
                                unit  = obs.unit,
                                data  = ocyc.mean(axis=0),
                                ndata = obs.ndata,
                                lat   = obs.lat,
                                lon   = obs.lon,
                                time  = ot,
                                time_bnds = otb)
            oiav     = Variable(name  = "iav", # deseasonalized interannual variability
                                unit  = obs.unit,
                                data  = obs.data-il.ExtendAnnualCycle(obs.time,ocyc.data,ocyc.time),
                                time  = obs.time,
                                ndata = obs.ndata,
                                lat   = obs.lat,
                                lon   = obs.lon,
                                time_bnds = obs.time_bnds)


            ocycf    = Variable(name  = "cycle_fine", # finely sampled cycle from cubic interpolation
                                unit  = obs.unit,
                                data  = obs_cyc,
                                time  = np.linspace(0,365,366),
                                ndata = obs.ndata,
                                lat   = obs.lat,
                                lon   = obs.lon)
            obs_amp  = Variable(name  = "amp", # mean amplitude over time period
                                unit  = obs.unit,
                                data  = obs_amp,
                                ndata = obs.ndata,
                                lat   = obs.lat,
                                lon   = obs.lon)
            obs_maxp = Variable(name  = "maxp", # Julian day of the maximum of the annual cycle
                                unit  = "d",
                                data  = obs_maxp,
                                ndata = obs.ndata,
                                lat   = obs.lat,
                                lon   = obs.lon)
            obs_minp = Variable(name  = "minp", # Julian day of the minimum of the annual cycle
                                unit  = "d",
                                data  = obs_minp,
                                ndata = obs.ndata,
                                lat   = obs.lat,
                                lon   = obs.lon)

            # Write out ILAMB variables for modeled quantities
            mcyc     = Variable(name  = "cycle", # mean annual cycle
                                unit  = mod.unit,
                                data  = mcyc.mean(axis=0),
                                ndata = mod.ndata,
                                lat   = mod.lat,
                                lon   = mod.lon,
                                time  = mt,
                                time_bnds = mtb)
            miav     = Variable(name  = "iav", # deseasonalized interannual variability
                                unit  = mod.unit,
                                data  = mod.data-il.ExtendAnnualCycle(mod.time,mcyc.data,mcyc.time),
                                time  = mod.time,
                                ndata = mod.ndata,
                                lat   = mod.lat,
                                lon   = mod.lon,
                                time_bnds = mod.time_bnds)

            mcycf    = Variable(name  = "cycle_fine", # finely sampled cycle from cubic interpolation
                                unit  = mod.unit,
                                data  = mod_cyc,
                                time  = np.linspace(0,365,366),
                                ndata = mod.ndata,
                                lat   = mod.lat,
                                lon   = mod.lon)
            mod_amp  = Variable(name  = "amp", # mean amplitude over time period
                                unit  = mod.unit,
                                data  = mod_amp,
                                ndata = mod.ndata,
                                lat   = mod.lat,
                                lon   = mod.lon)
            mod_maxp = Variable(name  = "maxp", # Julian day of the maximum of the annual cycle
                                unit  = "d",
                                data  = mod_maxp,
                                ndata = mod.ndata,
                                lat   = mod.lat,
                                lon   = mod.lon)
            mod_minp = Variable(name  = "minp", # Julian day of the minimum of the annual cycle
                                unit  = "d",
                                data  = mod_minp,
                                ndata = mod.ndata,
                                lat   = mod.lat,
                                lon   = mod.lon)


        #ccgcrv trend, longtime scale, seasonal (harmonics), smooth data for obs
        #placeholder for ccgObsTrend, ccgObsPoly, ccgObsIav, ccgObsFun, ccgObsHarmo, ccgObsSmooth
        ccgObsTrend = copy.deepcopy(obs)
        ccgObsTrend.name = "ccgTrend"

        ccgObsPoly = copy.deepcopy(obs)
        ccgObsPoly.name = "ccgPoly"

        ccgObsIav = copy.deepcopy(obs)
        ccgObsIav.name = "ccgIav"

        ccgObsFun = copy.deepcopy(obs)
        ccgObsFun.name = "ccgFun"

        ccgObsHarmo = copy.deepcopy(obs)
        ccgObsHarmo.name = "ccgHarmo"

        ccgObsSmooth = copy.deepcopy(obs)
        ccgObsSmooth.name = "ccgSmooth"



        for ss in range(0, obs.ndata):
            whrData = np.where(obs.data[:,ss].mask==False)
            xp=(obs.time/365)[whrData]
            yp=obs.data[:,ss][whrData]

            #long term filter is 10 yrs
            yrLongTerm = 10
            filt = ccgfilt.ccgFilter(xp=xp,
                                     yp=yp,
                                     longterm = (yrLongTerm*365),
                                     debug=False)

            ccgObsTrend.data[whrData,ss] = filt.getTrendValue(xp)
            ccgObsPoly.data[whrData,ss] = filt.getPolyValue(xp)
            ccgObsFun.data[whrData,ss] = filt.getFunctionValue(xp)
            ccgObsHarmo.data[whrData,ss] = filt.getHarmonicValue(xp)
            ccgObsSmooth.data[whrData,ss] = filt.getSmoothValue(xp)
            ccgObsIav.data[whrData,ss] = ccgObsSmooth.data[whrData,ss] - ccgObsTrend.data[whrData,ss] - ccgObsHarmo.data[whrData,ss]
            ccgCrossDates = filt.getTrendCrossingDates()

            #write out crossingdates and BRW amplitude or not?
            flag_write = False
            if flag_write:
                #write out Crossingdates
                if not os.path.exists((os.path.join(self.output_path, "ccg/"))):
                    os.mkdir((os.path.join(self.output_path, "ccg/")))

                if not os.path.exists((os.path.join(self.output_path, "ccg/CrossingDates/"))):
                    os.mkdir((os.path.join(self.output_path, "ccg/CrossingDates/")))


                np.savetxt(os.path.join(self.output_path,"ccg/CrossingDates/ccgCrossDates_%s_benchmark_site_%s.csv" % (self.name, ss)), ccgCrossDates, delimiter= ",", fmt= '%s')

                #write out BRW amplitude:
                if ss == 5:
                    ccgAmplitude = filt.getAmplitudes()
                    if not os.path.exists((os.path.join(self.output_path, "ccg/seaCyleAmplitudeTrend/"))):
                          os.mkdir((os.path.join(self.output_path, "ccg/seaCyleAmplitudeTrend/")))
                    np.savetxt(os.path.join(self.output_path,"ccg/seaCyleAmplitudeTrend/ccgAmplitude_%s_benchmark_lat%s.csv" % (self.name, obs.lat[ss])), ccgAmplitude, delimiter= ",", header="year, total_amplitude, max_date, max_value, min_date, min_value")




        #ccgcrv trend, longtime scale, seasonal (harmonics), smooth data for mod
        ccgModTrend = copy.deepcopy(mod)
        ccgModTrend.name = "ccgTrend"

        ccgModPoly = copy.deepcopy(mod)
        ccgModPoly.name = "ccgPoly"

        ccgModIav = copy.deepcopy(mod)
        ccgModIav.name = "ccgIav"

        ccgModFun = copy.deepcopy(mod)
        ccgModFun.name = "ccgFun"

        ccgModHarmo = copy.deepcopy(mod)
        ccgModHarmo.name = "ccgHarmo"

        ccgModSmooth = copy.deepcopy(mod)
        ccgModSmooth.name = "ccgSmooth"



        for ss in range(0, mod.ndata):

            whrData = np.where(mod.data[:,ss].mask==False)

            if len(whrData) > 0:
                xp=(mod.time/365)[whrData]
                yp=mod.data[:,ss][whrData]
                #long term filter is 10 yrs
                yrLongTerm = 10
                filt = ccgfilt.ccgFilter(xp=xp,
                                         yp=yp,
                                         longterm = (yrLongTerm*365),
                                         debug=False)

                ccgModTrend.data[whrData,ss] = filt.getTrendValue(xp)
                ccgModPoly.data[whrData,ss] = filt.getPolyValue(xp)

                ccgModFun.data[whrData,ss] = filt.getFunctionValue(xp)
                ccgModHarmo.data[whrData,ss] = filt.getHarmonicValue(xp)
                ccgModSmooth.data[whrData,ss] = filt.getSmoothValue(xp)
                ccgModIav.data[whrData,ss] = ccgModSmooth.data[whrData,ss] - ccgModTrend.data[whrData,ss] - ccgModHarmo.data[whrData,ss]

                #write out or not?
                if flag_write:
                    ccgCrossDates = filt.getTrendCrossingDates()
                    np.savetxt(os.path.join(self.output_path,"ccg/CrossingDates/ccgCrossDates_%s_%s_site_%s.csv" % (self.name, m.name, ss)), ccgCrossDates, delimiter= ",", fmt= '%s')


                    if ss == 5:
                        ccgAmplitude = filt.getAmplitudes()
                        np.savetxt(os.path.join(self.output_path,"ccg/seaCyleAmplitudeTrend/ccgAmplitude_%s_%s_lat%s.csv" % (self.name,m.name, mod.lat[ss])), ccgAmplitude, delimiter= ",", header="year, total_amplitude, max_date, max_value, min_date, min_value")


        if flag_write:
            results = Dataset(os.path.join(self.output_path,"ccg/ccg_%s_%s.nc" % (self.name,m.name)), mode="w")
            for v in [ccgModTrend, ccgModPoly, ccgModIav, ccgModFun, ccgModHarmo, ccgModSmooth]:
                v.toNetCDF4(results)
            results.close()


            results = Dataset(os.path.join(self.output_path,"ccg/ccg_%s_Benchmark.nc" % self.name), mode="w")
            for v in [ccgObsTrend, ccgObsPoly, ccgObsIav, ccgObsFun, ccgObsHarmo, ccgObsSmooth]:
                v.toNetCDF4(results)
            results.close()


        #replace ILAMB iav with ccg IAV:
        oiav.data = ccgObsIav.data
        miav.data = ccgModIav.data



        #calculate score:
        # Amplitude score: for each site we compute the relative error
        # in amplitude and then score each site using the
        # exponential. The score for the model is then the arithmetic
        # mean across sites.
        #SampTmp = -np.clip(np.abs(mod_amp.data-obs_amp.data)/obs_amp.data, 0, 6)

        #avoid underflow error
        with np.errstate(under='ignore'):
            Samp = Variable(name  = "Amplitude Score global",
                            unit  = "1",
                            data  = np.exp(-np.abs(mod_amp.data-obs_amp.data)/obs_amp.data).mean())



            # Interannual variability score: similar to the amplitude
            # score, we also score the relative error in the stdev(iav)
            # and report a mean across sites.
            ostd = oiav.data.std(axis=0)
            mstd = miav.data.std(axis=0)
            Siav = Variable(name  = "Interannual Variability Score global",
                            unit  = "1",
                            data  = np.exp(-np.abs(mstd-ostd)/ostd).mean())


            # Min/Max Phase score: for each site we compute the phase
            # shift and normalize it linearly where a 0 day shift gets a
            # score of 1 and a 365/2 day shift is zero. We then compute a
            # weighted mean across sites where sites without a well
            # defined annual cycle are discarded.
            Smax = Variable(name = "Max Phase Score global",
                            unit = "1",
                            data = np.average(_computeShift(obs_maxp,mod_maxp),weights=well_define))
            Smin = Variable(name = "Min Phase Score global",
                            unit = "1",
                            data = np.average(_computeShift(obs_minp,mod_minp),weights=well_define))



        # Write out the intermediate variables
        with Dataset(os.path.join(self.output_path,"%s_%s.nc" % (self.name,m.name)),mode="w") as results:
            results.setncatts({"name" :m.name, "color":m.color, "weight":self.cweight})
            for v in [mod,mcyc,miav,mcycf,mod_maxp,mod_minp,mod_amp,Samp,Siav,Smax,Smin]:
                v.toNetCDF4(results,group="MeanState")
            results.setncattr("complete",1)
        if not self.master: return
        with Dataset(os.path.join(self.output_path,"%s_Benchmark.nc" % self.name),mode="w") as results:
            results.setncatts({"name" :"Benchmark", "color":np.asarray([0.5,0.5,0.5]),"weight":self.cweight})
            for v in [obs,ocyc,oiav,ocycf,obs_maxp,obs_minp,obs_amp]:
                v.toNetCDF4(results,group="MeanState")
            results.setncattr("complete",1)


    def modelPlots(self,m):

        # Check that the required intermediate files are present
        bname  = "%s/%s_Benchmark.nc" % (self.output_path,self.name)
        fname  = "%s/%s_%s.nc" % (self.output_path,self.name,m.name)
        if not os.path.isfile(bname): return
        if not os.path.isfile(fname): return

        # Get the HTML page
        page = [page for page in self.layout.pages if "MeanState" in page.name][0]

        # Read variables from the datafiles
        obs   = Variable(filename=bname,variable_name="co2"       ,groupname="MeanState")
        mod   = Variable(filename=fname,variable_name="co2"       ,groupname="MeanState")
        ocyc  = Variable(filename=bname,variable_name="cycle"     ,groupname="MeanState")
        mcyc  = Variable(filename=fname,variable_name="cycle"     ,groupname="MeanState")
        oiav  = Variable(filename=bname,variable_name="iav"       ,groupname="MeanState")
        miav  = Variable(filename=fname,variable_name="iav"       ,groupname="MeanState")
        ocycf = Variable(filename=bname,variable_name="cycle_fine",groupname="MeanState")
        mcycf = Variable(filename=fname,variable_name="cycle_fine",groupname="MeanState")
        omaxp = Variable(filename=bname,variable_name="maxp"      ,groupname="MeanState")
        ominp = Variable(filename=bname,variable_name="minp"      ,groupname="MeanState")
        oamp  = Variable(filename=bname,variable_name="amp"       ,groupname="MeanState")
        mmaxp = Variable(filename=fname,variable_name="maxp"      ,groupname="MeanState")
        mminp = Variable(filename=fname,variable_name="minp"      ,groupname="MeanState")
        mamp  = Variable(filename=fname,variable_name="amp"       ,groupname="MeanState")
        t     = np.linspace(0,365,366)

        # Create an index for ordering sites by descending latitude
        sord  = np.argsort(obs.lat)[::-1]
        inds  = np.asarray(range(len(self.lbls)),dtype=int)[sord]
        lbls  = np.asarray(self.lbls)[sord]

        # Create sparkline plots of each site
        fig_height     = 1.
        width_per_year = 5./28
        fig_dpi        = 300.
        lw             = 1.
        bndmonths      = np.asarray(bnd_months,dtype=float)/365.
        for site_id,site in zip(inds,lbls):

            # Initialize site info
            band    = self.lat_bands.searchsorted(obs.lat[site_id])
            section = "Latitude Band %d to %d [ppm]" % (self.lat_bands[band-1],self.lat_bands[band])
            vmin   = min(obs.data[:,site_id].min(),mod.data[:,site_id].min())
            vmax   = max(obs.data[:,site_id].max(),mod.data[:,site_id].max())
            tick   = max(int(np.floor(min(vmax,abs(vmin)))),1)
            yticks = [-tick,0,tick]

            # How many years of data do we have?
            t0,tf = mod.time_bnds[(np.where((mod.data[:,site_id]*mod.time).mask==False)[0])[[0,-1]]]/365.+1850
            t0    = np.floor(t0[ 0])
            tf    = np.ceil (tf[-1])
            xticks = [i for i in range(int(t0),int(tf)+1) if str(i)[-1]=="0"]

            # Plot setup
            fig_width0 = (5.   )*width_per_year
            fig_width1 = (tf-t0)*width_per_year
            fig_width2 = (tf-t0)*width_per_year
            fig_width3 = (10.)  *width_per_year
            fig,ax = plt.subplots(ncols   = 4,
                                  figsize = (fig_width0+
                                             fig_width1+
                                             fig_width2+
                                             fig_width3,fig_height),
                                  gridspec_kw  = {'width_ratios':[fig_width0,fig_width3,fig_width1,fig_width2]},
                                  tight_layout = True,
                                  dpi = fig_dpi)

            # Text only plot with the name and location of the site
            ax[0].text(0.5,0.5,"%s\n%d,%d" % (site,obs.lat[site_id],obs.lon[site_id]),
                       horizontalalignment = 'center',
                       verticalalignment   = 'center',
                       transform=ax[0].transAxes)
            ax[0].set_xticks([])
            ax[0].set_yticks([])
            ax[0].axis(False)

            # Plot the finely interpolated annual cycle, shade JFM and JJA
            ax[1].fill_between(bndmonths[[0,3]],[vmin,vmin],[vmax,vmax],color='k',alpha=0.05,lw=0)
            ax[1].fill_between(bndmonths[[6,9]],[vmin,vmin],[vmax,vmax],color='k',alpha=0.05,lw=0)
            ax[1].plot(ocyc.time/365,ocyc.data[:,site_id],lw=1.5*lw,color='k',alpha=0.35)
            ax[1].plot(mcyc.time/365,mcyc.data[:,site_id],lw=lw,color=m.color)
            ax[1].set_ylim(vmin,vmax)
            ax[1].spines['top'  ].set_visible(False)
            ax[1].spines['right'].set_visible(False)
            ax[1].spines['bottom'].set_position('zero')
            ax[1].set_xticks([])
            ax[1].set_yticks(yticks)
            ax[1].set_xticklabels([])
            ax[1].set_ylabel('cycle')

            # Plot the variability in co2, shade every other decade
            shade = [t0,]+xticks+[tf,]
            alf   = 0.15
            bot   = vmin + 0.02*(vmax-vmin)
            for i in range(1,len(shade)-1):
                if i % 2 == 0:
                    ax[2].text(shade[i],bot,shade[i],color='k',alpha=alf,size=12)
                    ax[3].text(shade[i],bot,shade[i],color='k',alpha=alf,size=12)
                else:
                    ax[2].fill_between(shade[i:(i+2)],[vmin,vmin],[vmax,vmax],color='k',alpha=0.05,lw=0)
                    ax[2].text(shade[i],bot,shade[i],color='k',alpha=alf,size=12)
                    ax[3].fill_between(shade[i:(i+2)],[vmin,vmin],[vmax,vmax],color='k',alpha=0.05,lw=0)
                    ax[3].text(shade[i],bot,shade[i],color='k',alpha=alf,size=12)

            ax[2].plot(obs.time/365+1850,obs.data[:,site_id],lw=1.5*lw,color='k',alpha=0.35)
            ax[2].plot(mod.time/365+1850,mod.data[:,site_id],lw=lw,color=m.color)
            ax[2].set_ylim(vmin,vmax)
            ax[2].spines['top'  ].set_visible(False)
            ax[2].spines['right'].set_visible(False)
            ax[2].spines['bottom'].set_position('zero')
            ax[2].set_yticks(yticks)
            ax[2].set_xticklabels([])
            ax[2].set_xticks([])
            ax[2].set_ylabel('var')

            # Plot the interannual variability in co2, shade every other decade
            ax[3].plot(oiav.time/365+1850,oiav.data[:,site_id],lw=1.5*lw,color='k',alpha=0.35)
            ax[3].plot(miav.time/365+1850,miav.data[:,site_id],lw=lw,color=m.color)
            ax[3].set_ylim(vmin,vmax)
            ax[3].spines['top'  ].set_visible(False)
            ax[3].spines['right'].set_visible(False)
            ax[3].spines['bottom'].set_position('zero')
            ax[3].set_xticks([])
            ax[3].set_yticks(yticks)
            ax[3].tick_params(axis='x',direction='inout',length=10)
            ax[3].set_ylabel('iav')

            # Save the figure
            fig.savefig(os.path.join(self.output_path,"%s_global_%s.png" % (m.name,site)))
            page.addFigure(section,
                           site,
                           "MNAME_global_%s.png" % site,
                           side   = "",
                           legend = False,
                           width  = fig.get_size_inches()[0]*fig.dpi*0.25,
                           br     = True,
                           longname = "Site %s" % site)
            plt.close()


        # Compute mean amplitude, max and min phase over latitude bands
        lat_bnds = self.lat_bands
        lat      = 0.5*(lat_bnds[:-1]+lat_bnds[1:])
        nb       = lat_bnds.size-1
        o_band_min = np.zeros(nb); o_band_max = np.zeros(nb); o_band_amp = np.zeros(nb); o_band_iav = np.zeros(nb)
        m_band_min = np.zeros(nb); m_band_max = np.zeros(nb); m_band_amp = np.zeros(nb); m_band_iav = np.zeros(nb)
        with np.errstate(under='ignore'):
            for i in range(o_band_min.size):
                ind  = np.where((obs.lat >  lat_bnds[i  ])*
                                (obs.lat <= lat_bnds[i+1]))[0]
                o_band_min[i] = _meanDay(ominp.data[ind])
                o_band_max[i] = _meanDay(omaxp.data[ind])
                o_band_amp[i] =           oamp.data[ind].mean()
                o_band_iav[i] =           oiav.data.std(axis=0)[ind].mean()
                m_band_min[i] = _meanDay(mminp.data[ind])
                m_band_max[i] = _meanDay(mmaxp.data[ind])
                m_band_amp[i] =           mamp.data[ind].mean()
                m_band_iav[i] =           miav.data.std(axis=0)[ind].mean()



        # To plot the mean values over latitude bands superimposed on
        # the globe, we have to transform the phase and amplitude
        # values to [-180,180], as if they were longitudes.
        o_band_min = o_band_min/365.*360-180
        o_band_max = o_band_max/365.*360-180
        m_band_min = m_band_min/365.*360-180
        m_band_max = m_band_max/365.*360-180

        max_amp    = o_band_amp.max()
        min_amp    = o_band_amp.min()
        amp_ticks = np.linspace(min_amp,max_amp,6)
        amp_ticklabels = ["%.2f" % t for t in amp_ticks]
        damp     = 0.1*(max_amp - min_amp)
        max_amp += damp
        min_amp -= damp
        o_band_amp = (o_band_amp-min_amp)/(max_amp-min_amp)*360-180
        m_band_amp = (m_band_amp-min_amp)/(max_amp-min_amp)*360-180
        amp_ticks  = (amp_ticks -min_amp)/(max_amp-min_amp)*360-180

        max_iav    = max(o_band_iav.max(),m_band_iav.max())
        min_iav    = 0.
        iav_ticks = np.linspace(min_iav,max_iav,6)
        iav_ticklabels = ["%.2f" % t for t in iav_ticks]
        diav     = 0.1*(max_iav - min_iav)
        max_iav += diav
        min_iav -= diav
        o_band_iav = (o_band_iav-min_iav)/(max_iav-min_iav)*360.-180.
        m_band_iav = (m_band_iav-min_iav)/(max_iav-min_iav)*360.-180.
        iav_ticks  = (iav_ticks -min_iav)/(max_iav-min_iav)*360.-180.

        # Plot mean latitude band amplitude where amplitude is on the longitude axis
        fig,ax = plt.subplots(figsize=(8,4.5),tight_layout=True,subplot_kw={'projection':ccrs.PlateCarree()})
        ax.add_feature(cfeature.NaturalEarthFeature('physical','land','110m',
                                                    edgecolor='face',
                                                    facecolor='0.875'),zorder=-1)
        ax.add_feature(cfeature.NaturalEarthFeature('physical','ocean','110m',
                                                    edgecolor='face',
                                                    facecolor='1.000'),zorder=-1)
        ax.set_extent([-180,+180,-90,+90],ccrs.PlateCarree())
        ms = 8
        ax.scatter(obs.lon,obs.lat,8,color="0.60",label="Sites")
        ax.plot(o_band_amp,lat,'--o',color=np.asarray([0.5,0.5,0.5]),label="%s amplitude" % self.name,mew=0,markersize=ms)
        ax.plot(m_band_amp,lat,'-o' ,color=m.color,label="%s amplitude" % m.name,mew=0,markersize=ms)
        ax.yaxis.grid(color="0.875",linestyle="-")
        ax.legend(bbox_to_anchor=(0,1.005,1,0.25),loc='lower left',mode='expand',ncol=5,borderaxespad=0,frameon=False)
        ax.set_xlim(-180,180)
        ax.set_ylim(-90,90)
        ax.set_xlabel(obs.unit)
        ax.set_xticks(amp_ticks)
        ax.set_xticklabels(amp_ticklabels)
        ax.set_yticks(lat_bnds)
        fig.savefig(os.path.join(self.output_path,"%s_global_amp.png" % m.name))
        plt.close()
        page.addFigure("Summary",
                       "amp",
                       "MNAME_RNAME_amp.png",
                       side   = "AMPLITUDE",
                       width  = fig.get_size_inches()[0]*fig.dpi*0.75,
                       legend = False,
                       longname = "Amplitude")

        # Plot mean latitude band iav where iav is on the longitude axis

        fig,ax = plt.subplots(figsize=(8,4.5),tight_layout=True,subplot_kw={'projection':ccrs.PlateCarree()})
        ax.add_feature(cfeature.NaturalEarthFeature('physical','land','110m',
                                                    edgecolor='face',
                                                    facecolor='0.875'),zorder=-1)
        ax.add_feature(cfeature.NaturalEarthFeature('physical','ocean','110m',
                                                    edgecolor='face',
                                                    facecolor='1.000'),zorder=-1)
        ax.set_extent([-180,+180,-90,+90],ccrs.PlateCarree())
        ms = 8
        ax.scatter(obs.lon,obs.lat,8,color="0.60",label="Sites")
        ax.plot(o_band_iav,lat,'--o',color=np.asarray([0.5,0.5,0.5]),label="%s variability" % self.name,mew=0,markersize=ms)
        ax.plot(m_band_iav,lat,'-o' ,color=m.color,label="%s variability" % m.name,mew=0,markersize=ms)
        ax.yaxis.grid(color="0.875",linestyle="-")
        ax.legend(bbox_to_anchor=(0,1.005,1,0.25),loc='lower left',mode='expand',ncol=5,borderaxespad=0,frameon=False)
        ax.set_xlim(-180,180)
        ax.set_ylim(-90,90)
        ax.set_xlabel(obs.unit)
        ax.set_xticks(iav_ticks)
        ax.set_xticklabels(iav_ticklabels)
        ax.set_yticks(lat_bnds)
        fig.savefig(os.path.join(self.output_path,"%s_global_iav.png" % m.name))
        plt.close()
        page.addFigure("Summary",
                       "iav",
                       "MNAME_RNAME_iav.png",
                       side   = "INTERANNUAL VARIABILITY",
                       width  = fig.get_size_inches()[0]*fig.dpi*0.75,
                       legend = False)

        # Plot mean latitude band max phase where the phase is on the longitude axis
        fig,ax = plt.subplots(figsize=(8,4.5),tight_layout=True,subplot_kw={'projection':ccrs.PlateCarree()})
        ax.add_feature(cfeature.NaturalEarthFeature('physical','land','110m',
                                                    edgecolor='face',
                                                    facecolor='0.875'),zorder=-1)
        ax.add_feature(cfeature.NaturalEarthFeature('physical','ocean','110m',
                                                    edgecolor='face',
                                                    facecolor='1.000'),zorder=-1)
        ax.set_extent([-180,+180,-90,+90],ccrs.PlateCarree())
        ms = 8
        ax.scatter(obs.lon,obs.lat,8,color="0.60",label="Sites")
        ax.plot(o_band_max,lat,'--o',color=np.asarray([0.5,0.5,0.5]),label="%s maximum" % self.name,mew=0,markersize=ms)
        ax.plot(m_band_max,lat,'-o' ,color=m.color,label="%s maximum" % m.name,mew=0,markersize=ms)
        ax.yaxis.grid(color="0.875",linestyle="-")
        ax.legend(bbox_to_anchor=(0,1.005,1,0.25),loc='lower left',mode='expand',ncol=3,borderaxespad=0,frameon=False)
        ax.set_xlim(-180,180)
        ax.set_ylim(-90,90)
        ax.set_xticks(mid_months/365.*360.-180)
        ax.set_xticklabels(lbl_months)
        ax.set_yticks(lat_bnds)
        fig.savefig(os.path.join(self.output_path,"%s_global_maxphase.png" % m.name))
        plt.close()
        page.addFigure("Summary",
                       "maxphase",
                       "MNAME_RNAME_maxphase.png",
                       side   = "TIMING OF MAXIMUM",
                       width  = fig.get_size_inches()[0]*fig.dpi*0.75,
                       legend = False,
                       longname = "Timing of maximum phase")

        # Plot mean latitude band min phase where the phase is on the longitude axis
        fig,ax = plt.subplots(figsize=(8,4.5),tight_layout=True,subplot_kw={'projection':ccrs.PlateCarree()})
        ax.add_feature(cfeature.NaturalEarthFeature('physical','land','110m',
                                                    edgecolor='face',
                                                    facecolor='0.875'),zorder=-1)
        ax.add_feature(cfeature.NaturalEarthFeature('physical','ocean','110m',
                                                    edgecolor='face',
                                                    facecolor='1.000'),zorder=-1)
        ax.set_extent([-180,+180,-90,+90],ccrs.PlateCarree())
        ms = 8
        ax.scatter(obs.lon,obs.lat,8,color="0.60",label="Sites")
        ax.plot(o_band_min,lat,'--o',color=np.asarray([0.5,0.5,0.5]),label="%s minimum" % self.name,mew=0,markersize=ms)
        ax.plot(m_band_min,lat,'-o' ,color=m.color,label="%s minimum" % m.name,mew=0,markersize=ms)
        ax.yaxis.grid(color="0.875",linestyle="-")
        ax.legend(bbox_to_anchor=(0,1.005,1,0.25),loc='lower left',mode='expand',ncol=3,borderaxespad=0,frameon=False)
        ax.set_xlim(-180,180)
        ax.set_ylim(-90,90)
        ax.set_xticks(mid_months/365.*360.-180)
        ax.set_xticklabels(lbl_months)
        ax.set_yticks(lat_bnds)
        fig.savefig(os.path.join(self.output_path,"%s_global_minphase.png" % m.name))
        plt.close()
        page.addFigure("Summary",
                       "minphase",
                       "MNAME_RNAME_minphase.png",
                       side   = "TIMING OF MINIMUM",
                       width  = fig.get_size_inches()[0]*fig.dpi*0.75,
                       legend = False,
                       longname = "Timing of minimum phase")

    def compositePlots(self):
        pass






